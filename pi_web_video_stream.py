import argparse
import io
import socket
import time
from threading import Lock

import cv2
from flask import Flask, Response


def try_picamera2(width: int, height: int, fps: int):
    """Try to open Pi Camera (CSI) via picamera2. Returns capture object or None."""
    try:
        from picamera2 import Picamera2
        cam = Picamera2()

        # Sensor mode 1 (1640x1232) covers the full 3280x2464 sensor = maximum FOV.
        # Mode 0 (640x480) only uses a center crop and cannot be overridden by ScalerCrop.
        full_sensor_mode = cam.sensor_modes[1]

        config = cam.create_video_configuration(
            main={"size": (width, height), "format": "XBGR8888"},
            raw={"size": full_sensor_mode["size"]},
            controls={"FrameDurationLimits": (int(1e6 / fps), int(1e6 / fps))},
        )
        cam.configure(config)
        cam.start()
        time.sleep(1)  # warm up
        return cam
    except Exception as e:
        print(f"[picamera2] not available: {e}")
        return None



class CameraStream:
    def __init__(self, device: int, width: int, height: int, fps: int):
        self.pi_cam = try_picamera2(width, height, fps)
        self.capture = None

        if self.pi_cam is None:
            # Fall back to OpenCV VideoCapture (USB webcam)
            self.capture = cv2.VideoCapture(device)
            if not self.capture.isOpened():
                raise RuntimeError(f"Could not open camera device {device}")
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.capture.set(cv2.CAP_PROP_FPS, fps)
            print(f"[camera] Using OpenCV VideoCapture on /dev/video{device}")
        else:
            print("[camera] Using picamera2 (CSI camera)")

        self.lock = Lock()

    def read_jpeg(self) -> bytes | None:
        with self.lock:
            if self.pi_cam is not None:
                frame = self.pi_cam.capture_array()   # XBGR8888: channels [R,G,B,X]
                frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)  # drop X, swap to BGR
            else:

                ok, frame = self.capture.read()
                if not ok:
                    return None

        ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return None
        return jpeg.tobytes()

    def close(self) -> None:
        with self.lock:
            if self.pi_cam is not None:
                self.pi_cam.stop()
                self.pi_cam.close()
            if self.capture is not None:
                self.capture.release()


def build_app(camera: CameraStream, frame_delay: float) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index() -> str:
        return (
            "<html><head><title>Pi Camera Stream</title></head>"
            "<body style='margin:0;background:#111;color:#eee;font-family:Arial,sans-serif;'>"
            "<div style='padding:10px 14px;'>"
            "<h3 style='margin:0 0 8px;'>Raspberry Pi Live Video</h3>"
            "<p style='margin:0;opacity:0.8;'>Open this page from any device on the same Ethernet network.</p>"
            "</div>"
            "<img src='/video_feed' style='width:100%;height:auto;display:block;' />"
            "</body></html>"
        )

    @app.route("/video_feed")
    def video_feed() -> Response:
        def generate():
            while True:
                frame = camera.read_jpeg()
                if frame is None:
                    time.sleep(frame_delay)
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
                time.sleep(frame_delay)

        return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve Raspberry Pi camera stream over HTTP")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8090, help="Bind port")
    parser.add_argument("--device", type=int, default=0, help="Camera device index (USB fallback)")
    parser.add_argument("--width", type=int, default=640, help="Frame width")
    parser.add_argument("--height", type=int, default=480, help="Frame height")
    parser.add_argument("--fps", type=int, default=20, help="Target stream FPS")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame_delay = 1.0 / max(args.fps, 1)
    camera = CameraStream(args.device, args.width, args.height, args.fps)
    app = build_app(camera, frame_delay)
    local_ip = get_local_ip()

    print("\nRaspberry Pi video web stream is running")
    print(f"Open on same Ethernet network: http://{local_ip}:{args.port}")
    print(f"Localhost: http://127.0.0.1:{args.port}\n")

    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        camera.close()


if __name__ == "__main__":
    main()