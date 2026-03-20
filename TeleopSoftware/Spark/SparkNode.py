import os
import json
import serial
import time
import pickle
import numpy as np
import signal

# import rospy
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, Bool

class SparkNode(Node):
    def __init__(self):
        super().__init__('SparkNode')
        # Define a signal handler function
        def signal_handler(sig, frame):
            print("Stopped")
            exit()
        signal.signal(signal.SIGINT, signal_handler)

    def get_distance(self, current, previous, max):
        dist = current - previous
        if(dist > max/2):
            dist -= max
        elif(dist < -max/2):
            dist += max   
        return dist/max*2*np.pi

    def read_json_packet(self, con, max_packets=20):
        for _ in range(max_packets):
            payload = con.read_until(b'\x00')[:-1]
            if not payload:
                continue
            text = payload.decode('utf-8', errors='ignore').strip()
            if not text or not text.startswith('{'):
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("no JSON payload received")

    def main(self):
        dev = os.sys.argv[1]
        latentcy_enable = len(os.sys.argv) > 2
        def init_connection():
            con = serial.Serial(dev, 921600, timeout=1.0)
            time.sleep(1.5) # Allow ESP32 reset/boot chatter to settle
            con.reset_input_buffer()
            con.reset_output_buffer()
            return con
        con = None
        while True:
            try:
                if con is not None:
                    con.close()
                con = init_connection()
                data = self.read_json_packet(con)
                break
            except RuntimeError:
                print(f"Waiting for Spark JSON on {dev}")
                time.sleep(1.0)
        ID = str(data['ID']).strip().lower()
        print(f"Connected to Spark: {ID} ({dev})")
        # Get the location of this python filem
        path = os.path.dirname(os.path.abspath(__file__))
        pickle_path = os.path.join(path, "offsets_"+ID+".pickle")
        offsets, inverted = pickle.load(open(pickle_path, "rb"))
        num_angles = 7
        max_raw_angle = 2**14
        previous_raw_angles = offsets
        calculated_angles = [0.0]*num_angles

        if latentcy_enable:
            topic = f"Spark_angle_buffer/{ID}"
            print(f"Buffering angles on {topic}")
        else:
            topic = f"Spark_angle/{ID}"
        # rospy.init_node(f"Spark_{ID}", anonymous=True)
        # pose_publisher = rospy.Publisher(topic, Float32MultiArray, queue_size=1)
        # enable_publisher = rospy.Publisher(f"Spark_enable/{ID}", Bool, queue_size=1)
        pose_publisher = self.create_publisher(Float32MultiArray, topic, 1)
        enable_publisher = self.create_publisher(Bool, f"Spark_enable/{ID}", 1)

        try:
            exit_flag = False
            while not exit_flag:
                try:
                    data = self.read_json_packet(con, max_packets=5)
                except serial.SerialException:
                    print(f"Spark {ID} disconnected")
                    # rospy.sleep(1)
                    # rclpy.sleep(1)
                    time.sleep(1)
                    con = init_connection()
                    continue
                except RuntimeError:
                    continue
                raw_angles = data['values']

                if False in data['status']:
                    print(f"Spark {ID} has an error in the status: {data['status']}")
                if(previous_raw_angles is not None):
                    for i in range(num_angles):
                        # print(get_distance(raw_angles[i], previous_raw_angles[i], max_raw_angle))
                        calculated_angles[i] += inverted[i] * self.get_distance(raw_angles[i], previous_raw_angles[i], max_raw_angle)
                previous_raw_angles = raw_angles
                pose_publisher.publish(Float32MultiArray(data=calculated_angles))
                # print(data['enable_switch'])
                # enable_publisher.publish(Bool(data['enable_switch']))
                enable_publisher.publish(Bool(data=data['enable_switch']))
        finally:
            con.close()
            print("Connection closed")


if __name__ == '__main__':
    rclpy.init()
    node = SparkNode()
    node.main()
    node.destroy_node()
    rclpy.shutdown()
