#!/usr/bin/env python
import roslib; roslib.load_manifest("raven_2_teleop")
import rospy
import math
from numpy import *
import tf
import tf.transformations as tft
from sixense.msg import Calib, CalibPaddle
from raven_2_msgs.msg import *
from geometry_msgs.msg import Pose, Point, Quaternion
from raven_2_trajectory.srv import RecordTrajectory, RecordTrajectoryResponse
from optparse import OptionParser

SIDE_ACTIVE = [False,True]
SIDE_NAMES = ['left','right']

BASE_FRAME = '/0_link'
END_EFFECTOR_FRAME_PREFIX = '/instrument_shaft_'
END_EFFECTOR_FRAME_SUFFIX = ['L','R']

#CalibPaddle.START	= 0
#CalibPaddle.BUMPER   = 5
#CalibPaddle.JOYSTICK = 6

GRIP_CLOSE_BUTTON = [1,2]
GRIP_OPEN_BUTTON = [3,4]

START_RECORD_BUTTON = [2,1]
STOP_RECORD_BUTTON = [4,3]

SCALE_ADJUST_BUTTON = CalibPaddle.START
SCALE_UP_BUTTON = [3,4]
SCALE_DOWN_BUTTON = [1,2]
SCALE_RESET_BUTTON = CalibPaddle.JOYSTICK

DEFAULT_SCALE = .1


# left is paddle 0, right is paddle 1

class HydraTeleop:
	xoffset = 0
	yoffset = 0
	zoffset = 0
	last_msg = None
	start_recording_last = False
	stop_recording_last = False
	joystick_active = [True,True]
	recorder = None

	def __init__(self,listener,record=False, scale=None):
		self.scale = scale
		self.scale_increment = scale / 20
		self.scale_adjusting_mode = False
	
		
		self.pub = rospy.Publisher('raven_command', RavenCommand)

		# set up tf listener
		self.listener = listener
		
		if SIDE_ACTIVE[0]:
			end_effector_frame = END_EFFECTOR_FRAME_PREFIX + END_EFFECTOR_FRAME_SUFFIX[0]
		else:
			end_effector_frame = END_EFFECTOR_FRAME_PREFIX + END_EFFECTOR_FRAME_SUFFIX[1]

		print "waiting for transform from {0} to {1}".format(BASE_FRAME, end_effector_frame)
		tries = 0
		while not rospy.is_shutdown():
			tries += 1
			#print "Try #%d" % tries
			try:
				self.listener.waitForTransform(BASE_FRAME, end_effector_frame, rospy.Time(0), rospy.Duration(5.0))
				break
			except tf.Exception, e:
				continue

		print "subscribing to hydra"
		self.hydra_sub = rospy.Subscriber("hydra_calib", Calib, self.callback)
		
		if record:
			self.recorder = rospy.ServiceProxy("record_trajectory",RecordTrajectory)

		print "ready for action"


	def callback(self,msg):
		if self.last_msg is None: 
			self.last_msg = msg
			return

		#scale adjustment
		if self.scale_adjusting_mode:
			curr_scale = self.scale
			for i in xrange(2):
				paddle = msg.paddles[i]
				if paddle.buttons[SCALE_ADJUST_BUTTON] and not self.last_msg.paddles[i].buttons[SCALE_ADJUST_BUTTON]:
					self.scale_adjusting_mode = False
					print "Done adjusting scale!"
					self.last_msg = msg
					return
				
				if paddle.buttons[SCALE_RESET_BUTTON] and not self.last_msg.paddles[i].buttons[SCALE_RESET_BUTTON]:
					self.scale = DEFAULT_SCALE
				elif paddle.buttons[SCALE_UP_BUTTON[i]] and not self.last_msg.paddles[i].buttons[SCALE_UP_BUTTON[i]]:
					self.scale += self.scale_increment
				elif paddle.buttons[SCALE_DOWN_BUTTON[i]] and not self.last_msg.paddles[i].buttons[SCALE_DOWN_BUTTON[i]]:
					self.scale -= self.scale_increment
				else:
					time_diff = (msg.header.stamp - self.last_msg.header.stamp).to_sec()
					time_per_increment = 0.4
					#print time_diff / time_per_increment, time_diff
					increment = self.scale_increment * time_diff / time_per_increment
					self.scale += increment * paddle.joy[1]
			if self.scale < 0:
				self.scale = 0;
			if curr_scale != self.scale:
				print "Scale is now %1.4f" % self.scale 
			self.last_msg = msg
			return

		#print "got msg"
		
		raven_command = RavenCommand()
		raven_command.header.stamp = rospy.Time.now()
		raven_command.header.frame_id = BASE_FRAME

		active = [False,False]
		newly_active = [False,False]
		for i in xrange(2):
			if SIDE_ACTIVE[i] and msg.paddles[i].buttons[CalibPaddle.BUMPER]:
				active[i] = True
				newly_active[i] = not self.last_msg.paddles[i].buttons[CalibPaddle.BUMPER]
		grip = [0,0]

		for i in xrange(2):
				
			arm_cmd = ArmCommand()
			tool_cmd = ToolCommand()
			tool_cmd.absolute = False
			paddle = msg.paddles[i]
			
			if (not active[i]) and paddle.buttons[SCALE_ADJUST_BUTTON] and not self.last_msg.paddles[i].buttons[SCALE_ADJUST_BUTTON]:
				print "Adjusting scale!  Current value: %1.7f" % self.scale
				self.scale_adjusting_mode = True
				self.last_msg = msg
				return

			if paddle.buttons[CalibPaddle.JOYSTICK] and not self.last_msg.paddles[i].buttons[CalibPaddle.JOYSTICK]:
				print "That's the %s controller!" % SIDE_NAMES[i]

			if newly_active[i]:
				print "%s tool active" % SIDE_NAMES[i]
				# calculate current position of robot
				try:
					(trans,rot) = self.listener.lookupTransform(BASE_FRAME, END_EFFECTOR_FRAME_PREFIX + END_EFFECTOR_FRAME_SUFFIX[i], rospy.Time(0))
				except (tf.LookupException, tf.ConnectivityException):
					if i==0:
						print "no transform for left arm!"
					else:
						print "no transform for right!"
					return
				xcur, ycur, zcur = trans[0],trans[1],trans[2]
				xcmd, ycmd, zcmd = (paddle.transform.translation.x * self.scale,
									paddle.transform.translation.y * self.scale,
									paddle.transform.translation.z * self.scale)

				self.xoffset, self.yoffset, self.zoffset = xcur-xcmd, ycur-ycmd, zcur-zcmd

				#print "engaging %s arm"%self.arms[i].lr
			elif not paddle.buttons[CalibPaddle.BUMPER] and self.last_msg.paddles[i].buttons[CalibPaddle.BUMPER]:
				print "%s tool inactive" % SIDE_NAMES[i]


			if active[i] and not newly_active[i]:

				dx = (paddle.transform.translation.x  - self.last_msg.paddles[i].transform.translation.x) * self.scale
				dy = (paddle.transform.translation.y  - self.last_msg.paddles[i].transform.translation.y) * self.scale
				dz = (paddle.transform.translation.z  - self.last_msg.paddles[i].transform.translation.z) * self.scale

				x = paddle.transform.translation.x * self.scale + self.xoffset
				y = paddle.transform.translation.y * self.scale + self.yoffset
				z = paddle.transform.translation.z * self.scale + self.zoffset

				xx = paddle.transform.rotation.x
				yy = paddle.transform.rotation.y
				zz = paddle.transform.rotation.z
				ww = paddle.transform.rotation.w
				
				p = mat(array([dx,dy,dz,1])).transpose()
				
				T1 = mat(array([[0,1,0,0],  [-1,0,0,0],  [0,0, 1,0], [0,0,0,1]]))
				T2 = mat(array([[1,0,0,0],  [0,-1,0,0],  [0,0,-1,0], [0,0,0,1]]))
				
				qmat = mat(tft.quaternion_matrix(array([xx,yy,zz,ww])))
				
				p_t = array(T1 * p)[0:3].flatten().tolist()
				#q_t = array(tft.quaternion_from_matrix(T1 * qmat * T2)).flatten().tolist()
				q_t = array(tft.quaternion_from_matrix(T1 * qmat)).flatten().tolist()
				
				#tool_cmd.tool_pose = Pose(Point(dx,dy,dz),Quaternion(xx,yy,zz,ww))
				tool_cmd.tool_pose = Pose(Point(*p_t),Quaternion(*q_t))
				
				if paddle.buttons[GRIP_OPEN_BUTTON[i]]: grip[i] = 1
				if paddle.buttons[GRIP_CLOSE_BUTTON[i]]: grip[i] = -1

				if self.joystick_active[i]:
					if paddle.joy[1] and not self.last_msg.paddles[i].joy[1]:
						print "Insertion active"
					j_cmd = JointCommand()
					j_cmd.command_type = JointCommand.COMMAND_TYPE_VELOCITY
					j_cmd.value = paddle.joy[1]
					arm_cmd.joint_types.append(Constants.JOINT_TYPE_INSERTION)
					arm_cmd.joint_commands.append(j_cmd)

					if paddle.joy[0] and not self.last_msg.paddles[i].joy[0]:
						pass
						#print "Rotation active"
					#raven_command.joint_velocities[i].rotation = paddle.joy[0]
				
				#if paddle.buttons[CalibPaddle.START] or paddle.joy[0] or paddle.joy[1]:
				if paddle.trigger or paddle.joy[0] or paddle.joy[1]:
					tool_cmd.tool_pose.position = Point(0,0,0)

			arm_cmd.active = active[i] and SIDE_ACTIVE[i]
			tool_cmd.grasp = grip[i]
			arm_cmd.tool_command = tool_cmd
			raven_command.arms[i] = arm_cmd
			
			if self.recorder:
				try:
					if paddle.buttons[START_RECORD_BUTTON[i]] and not self.start_recording_last:
						resp = self.recorder(True,False,"")
						if resp.result == RecordTrajectoryResponse.SUCCESS:
							print "started recording"
						elif resp.result == RecordTrajectoryResponse.ERROR:
							print "recording did not start: %d" % resp.result
					elif paddle.buttons[STOP_RECORD_BUTTON[i]] and self.stop_recording_last:
						resp = self.recorder(False,False,"")
						if resp.result == RecordTrajectoryResponse.SUCCESS:
							print "stopped recording"
						elif resp.result == RecordTrajectoryResponse.ERROR:
							print "error while stopping recording: %d" % resp.result
				except rospy.ServiceException, e:
					print "Recording call failed: %s" % e
				
					
				

		#publish RavenCommand
		raven_command.pedal_down = active[0] or active[1]

		self.pub.publish(raven_command)
		
		self.last_msg = msg
		self.start_recording_last = msg.paddles[0].buttons[START_RECORD_BUTTON[0]] or msg.paddles[1].buttons[START_RECORD_BUTTON[1]]
		self.stop_recording_last = msg.paddles[0].buttons[STOP_RECORD_BUTTON[0]] or msg.paddles[1].buttons[STOP_RECORD_BUTTON[1]]

if __name__ == "__main__":
	rospy.init_node("teleop")
	
	parser = OptionParser()
	
	parser.add_option('-r','--record',action='store_true',default=False)
	
	parser.add_option('-s','--scale',default=DEFAULT_SCALE)
	
	(options,args) = parser.parse_args()

	listener = tf.TransformListener()

	HT = HydraTeleop(listener,record=options.record,scale=options.scale)
	rospy.spin()
	
	rospy.loginfo('shutting down')
