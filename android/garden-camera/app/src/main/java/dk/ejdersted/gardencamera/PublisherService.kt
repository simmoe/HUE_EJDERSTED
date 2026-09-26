package dk.ejdersted.gardencamera

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.lifecycle.LifecycleService
import java.util.concurrent.Executors

class PublisherService : LifecycleService() {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var camera: Camera2Engine
    private var facing = "environment"
    private var ready = false
    private val snap = Runnable { capture() }
    private val poll = Runnable { pollFacing() }

    override fun onCreate() {
        super.onCreate()
        camera = Camera2Engine(this)
        startInForeground()
        io.execute {
            facing = FacingPoll.read()
            Log.i("GardenCam", "initial facing=$facing")
            main.post { openCurrent() }
        }
        main.postDelayed(poll, 2500)
    }

    override fun onDestroy() {
        main.removeCallbacks(snap)
        main.removeCallbacks(poll)
        camera.close()
        io.shutdown()
        super.onDestroy()
    }

    private fun startInForeground() {
        val channelId = "garden-camera"
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(channelId, "Haven kamera", NotificationManager.IMPORTANCE_MIN),
        )
        val note = Notification.Builder(this, channelId)
            .setContentTitle("Haven kamera")
            .setContentText("Vidvinkel til Ejdersted")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
        } else {
            startForeground(1, note)
        }
    }

    private fun openCurrent() {
        ready = false
        main.removeCallbacks(snap)
        val front = facing == "user"
        val id = camera.pickId(front)
        if (id == null) {
            Log.e("GardenCam", "no camera for facing=$facing")
            return
        }
        camera.open(id) {
            ready = true
            main.post(snap)
        }
    }

    private fun pollFacing() {
        io.execute {
            val next = FacingPoll.read()
            Log.i("GardenCam", "poll facing=$next (have $facing)")
            main.post {
                if (next != facing) {
                    facing = next
                    Log.i("GardenCam", "facing $facing")
                    openCurrent()
                }
                main.postDelayed(poll, 2500)
            }
        }
    }

    private fun capture() {
        if (!ready) {
            main.postDelayed(snap, 2000)
            return
        }
        camera.capture { bytes ->
            HubPoster.postJpeg(bytes)
            main.postDelayed(snap, 2000)
        }
    }
}
