package dk.ejdersted.gardencamera

object FacingPoll {
    fun read(): String {
        val cfg = HubPoster.getHub("/api/config/firebase") ?: return "environment"
        val project = jsonString(cfg, "projectId") ?: return "environment"
        val key = jsonString(cfg, "apiKey").orEmpty()
        val url = buildString {
            append("https://firestore.googleapis.com/v1/projects/")
            append(project)
            append("/databases/(default)/documents/ejdersted/camera_garden")
            if (key.isNotEmpty()) {
                append("?key=")
                append(key)
            }
        }
        val doc = HubPoster.getPublic(url) ?: HubPoster.getHubUrl(url) ?: return "environment"
        val facing = Regex("\"facing\"\\s*:\\s*\\{\\s*\"stringValue\"\\s*:\\s*\"(user|environment)\"")
            .find(doc)
            ?.groupValues
            ?.get(1)
        return facing ?: "environment"
    }

    private fun jsonString(json: String, key: String): String? {
        return Regex("\"$key\"\\s*:\\s*\"([^\"]+)\"").find(json)?.groupValues?.get(1)
    }
}
