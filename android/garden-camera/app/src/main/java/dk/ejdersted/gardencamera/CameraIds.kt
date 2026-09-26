package dk.ejdersted.gardencamera

import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.util.Log

object CameraIds {
    // A12: Camera2/Chrome label camera 2 as "front", but it is the 115° UW.
    private const val A12_UW = "2"
    private const val A12_FRONT = "1"

    fun pick(manager: CameraManager, front: Boolean): String? {
        val ids = manager.cameraIdList
        for (id in ids) {
            val c = manager.getCameraCharacteristics(id)
            val facing = c.get(CameraCharacteristics.LENS_FACING)
            val focal = c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)?.joinToString()
            val size = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE)
            Log.i("GardenCam", "all id=$id facing=$facing focal=$focal px=$size")
        }
        if (front) {
            if (ids.contains(A12_FRONT)) return A12_FRONT
            return ids.firstOrNull { id ->
                manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                    CameraCharacteristics.LENS_FACING_FRONT && id != A12_UW
            }
        }
        if (ids.contains(A12_UW)) return A12_UW
        return ids.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                CameraCharacteristics.LENS_FACING_BACK
        }
    }
}
