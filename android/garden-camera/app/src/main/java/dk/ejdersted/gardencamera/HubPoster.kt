package dk.ejdersted.gardencamera

import java.net.URL
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

object HubPoster {
    private val hubs = listOf(
        "https://192.168.8.133:8443",
        "https://kolonihave-pi.tail7947c4.ts.net:8443",
    )

    private val ssl: SSLContext by lazy {
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(trust), SecureRandom())
        }
    }

    fun postJpeg(body: ByteArray): Boolean {
        for (hub in hubs) {
            if (postOnce("$hub/api/camera/snapshot", body)) return true
        }
        return false
    }

    fun getHub(path: String): String? {
        for (hub in hubs) {
            val body = getOnce("$hub$path", trustHub = true)
            if (body != null) return body
        }
        return null
    }

    fun getPublic(url: String): String? = getOnce(url, trustHub = false)

    fun getHubUrl(url: String): String? = getOnce(url, trustHub = true)

    private fun postOnce(url: String, body: ByteArray): Boolean {
        val conn = open(url, trustHub = true).apply {
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Content-Type", "image/jpeg")
        }
        return try {
            conn.outputStream.use { it.write(body) }
            conn.responseCode in 200..299
        } catch (_: Exception) {
            false
        } finally {
            conn.disconnect()
        }
    }

    private fun getOnce(url: String, trustHub: Boolean): String? {
        val conn = open(url, trustHub).apply {
            requestMethod = "GET"
        }
        return try {
            if (conn.responseCode !in 200..299) null
            else conn.inputStream.bufferedReader().use { it.readText() }
        } catch (_: Exception) {
            null
        } finally {
            conn.disconnect()
        }
    }

    private fun open(url: String, trustHub: Boolean): HttpsURLConnection {
        val conn = URL(url).openConnection() as HttpsURLConnection
        conn.connectTimeout = 4000
        conn.readTimeout = 5000
        if (trustHub) {
            conn.sslSocketFactory = ssl.socketFactory
            conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
        }
        return conn
    }
}
