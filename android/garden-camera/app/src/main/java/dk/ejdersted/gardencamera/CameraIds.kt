package dk.ejdersted.gardencamera

import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.util.Log

object CameraIds {
    // A12 Camera2: 0 = real back (garden). 1 and 2 are both front;
    // 2 is the wider living-room shot and the one we keep as FRONT.
    private const val A12_BACK = "0"
    private const val A12_FRONT = "2"

    fun pick(manager: CameraManager, front: Boolean): String? {
        val ids = manager.cameraIdList
        for (id in ids) {
            val c = manager.getCameraCharacteristics(id)
            val facing = c.get(CameraCharacteristics.LENS_FACING)
            val focal = c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.joinToString()
            val size = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
            Log.i("GardenCam", "all id=$id facing=$facing focal=$focal px=$size")
        }
        val want = if (front) A12_FRONT else A12_BACK
        if (ids.contains(want)) return want
        val facing = if (front) {
            CameraCharacteristics.LENS_FACING_FRONT
        } else {
            CameraCharacteristics.LENS_FACING_BACK
        }
        return ids.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) == facing
        }
    }
}
