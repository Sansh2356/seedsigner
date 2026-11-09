# import the necessary packages
import logging
from threading import Thread
import time

try:  # legacy camera stack (SeedSigner OS / Bullseye)
	from picamera.array import PiRGBArray
	from picamera import PiCamera
	LEGACY = True
except ImportError:  # libcamera stack (Raspberry Pi OS Bookworm): picamera2
	LEGACY = False

logger = logging.getLogger(__name__)


# Modified from: https://github.com/jrosebr1/imutils
class PiVideoStream:
	def __init__(self, resolution=(320, 240), framerate=32, format="bgr", **kwargs):
		if LEGACY:
			# initialize the camera
			self.camera = PiCamera(resolution=resolution, framerate=framerate, **kwargs)

			# initialize the stream
			self.rawCapture = PiRGBArray(self.camera, size=resolution)
			self.stream = self.camera.capture_continuous(self.rawCapture,
				format=format, use_video_port=True)
		else:
			from picamera2 import Picamera2
			self.camera = Picamera2()
			# picamera2 names formats by little-endian word order: "BGR888" is R,G,B bytes per pixel
			config = self.camera.create_video_configuration(
				main={"size": tuple(resolution), "format": "BGR888" if format == "rgb" else "RGB888"},
				controls={"FrameRate": framerate},
				buffer_count=2,  # the Pi Zero has little memory to spare
			)
			self.camera.configure(config)
			self.camera.start()

		# initialize the frame and the variable used to indicate
		# if the thread should be stopped
		self.frame = None
		self.should_stop = False
		self.is_stopped = True

	def start(self):
		# start the thread to read frames from the video stream
		t = Thread(target=self.update if LEGACY else self.update_picamera2, args=())
		t.daemon = True
		t.start()
		self.is_stopped = False
		return self

	def update(self):
		# keep looping infinitely until the thread is stopped
		for f in self.stream:
			# grab the frame from the stream and clear the stream in
			# preparation for the next frame
			self.frame = f.array
			self.rawCapture.truncate(0)

			# if the thread indicator variable is set, stop the thread
			# and release camera resources
			if self.should_stop:
				logger.info("PiVideoStream: closing everything")
				self.stream.close()
				self.rawCapture.close()
				self.camera.close()
				self.should_stop = False
				self.is_stopped = True
				return

	def update_picamera2(self):
		while not self.should_stop:
			self.frame = self.camera.capture_array("main")  # blocks until the next frame
		logger.info("PiVideoStream: closing everything")
		self.camera.stop()
		self.camera.close()
		self.should_stop = False
		self.is_stopped = True

	def read(self):
		# return the frame most recently read
		return self.frame

	def stop(self):
		# indicate that the thread should be stopped
		self.should_stop = True

		# Block in this thread until stopped
		while not self.is_stopped:
			time.sleep(0.01)



class Picamera2Still:
	"""Single-frame capture on the picamera2 stack (camera entropy, camera rotation preview)."""
	def __init__(self, resolution):
		from picamera2 import Picamera2
		self.camera = Picamera2()
		self.camera.configure(self.camera.create_still_configuration(main={"size": tuple(resolution)}, buffer_count=1))
		self.camera.start()

	def capture_pil(self):
		return self.camera.capture_image("main").convert("RGB")

	def close(self):
		self.camera.stop()
		self.camera.close()
