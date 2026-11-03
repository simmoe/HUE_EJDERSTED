package dk.ejdersted.gardencamera

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.ImageReader
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import android.view.WindowManager

class Camera2Engine(private val context: Context) {
    private val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val thread = HandlerThread("garden-cam2").apply { start() }
    private val handler = Handler(thread.looper)
    private var camera: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var reader: ImageReader? = null
    private var previewTexture: SurfaceTexture? = null
    private var previewSurface: Surface? = null
    private var jpegOrientation = 90

    fun pickId(front: Boolean): String? = CameraIds.pick(manager, front)

    @SuppressLint("MissingPermission")
    fun open(id: String, onReady: () -> Unit) {
        handler.post {
            closeLocked()
            val chars = manager.getCameraCharacteristics(id)
            jpegOrientation = jpegOrientation(chars)
            val reader = ImageReader.newInstance(1280, 960, ImageFormat.JPEG, 2)
            this.reader = reader
            val texture = SurfaceTexture(0)
            texture.setDefaultBufferSize(640, 480)
            previewTexture = texture
            val preview = Surface(texture)
            previewSurface = preview
            Log.i(TAG, "open camera $id jpegOri=$jpegOrientation")
            manager.openCamera(id, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    camera = device
                    device.createCaptureSession(
                        listOf(preview, reader.surface),
                        object : CameraCaptureSession.StateCallback() {
                            override fun onConfigured(s: CameraCaptureSession) {
                                session = s
                                val previewReq = device.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
                                previewReq.addTarget(preview)
                                s.setRepeatingRequest(previewReq.build(), null, handler)
                                onReady()
                            }

                            override fun onConfigureFailed(s: CameraCaptureSession) {
                                Log.e(TAG, "session failed for $id")
                            }
                        },
                        handler,
                    )
                }

                override fun onDisconnected(device: CameraDevice) {
                    device.close()
                    if (camera === device) camera = null
                }

                override fun onError(device: CameraDevice, error: Int) {
                    Log.e(TAG, "camera error $error")
                    device.close()
                    if (camera === device) camera = null
                }
            }, handler)
        }
    }

    fun capture(onJpeg: (ByteArray) -> Unit) {
        handler.post {
            val cam = camera ?: return@post
            val sess = session ?: return@post
            val r = reader ?: return@post
            r.setOnImageAvailableListener({
                val img = r.acquireLatestImage() ?: return@setOnImageAvailableListener
                val buf = img.planes[0].buffer
                val bytes = ByteArray(buf.remaining())
                buf.get(bytes)
                img.close()
                onJpeg(bytes)
            }, handler)
            val req = cam.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
            req.addTarget(r.surface)
            req.set(CaptureRequest.JPEG_ORIENTATION, jpegOrientation)
            sess.capture(req.build(), null, handler)
        }
    }

    fun close() {
        handler.post { closeLocked() }
    }

    private fun closeLocked() {
        try {
            session?.close()
        } catch (_: Exception) {
        }
        session = null
        try {
            camera?.close()
        } catch (_: Exception) {
        }
        camera = null
        reader?.close()
        reader = null
        previewSurface?.release()
        previewSurface = null
        previewTexture?.release()
        previewTexture = null
    }

    @Suppress("DEPRECATION")
    private fun jpegOrientation(chars: CameraCharacteristics): Int {
        val sensor = chars.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90
        val facing = chars.get(CameraCharacteristics.LENS_FACING)
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val device = when (wm.defaultDisplay.rotation) {
            Surface.ROTATION_0 -> 0
            Surface.ROTATION_90 -> 90
            Surface.ROTATION_180 -> 180
            Surface.ROTATION_270 -> 270
            else -> 0
        }
        return if (facing == CameraCharacteristics.LENS_FACING_FRONT) {
            (sensor + device) % 360
        } else {
            (sensor - device + 360) % 360
        }
    }

    companion object {
        private const val TAG = "GardenCam"
    }
}
