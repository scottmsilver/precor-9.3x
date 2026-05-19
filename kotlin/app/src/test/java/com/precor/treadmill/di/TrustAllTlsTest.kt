package com.precor.treadmill.di

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import javax.net.ssl.SSLHandshakeException

/**
 * Regression test for the per-device self-signed cert trust path.
 *
 * The treadmill Pi serves a self-signed cert (no CA). Before trustAllTls(),
 * the OkHttpClient had only a hostname verifier — which bypasses CN/SAN
 * mismatch but NOT chain validation — so connections failed with
 * `CertPathValidatorException: Trust anchor for certification path not found`
 * and neither REST nor the (shared) WebSocket client could reach the Pi.
 */
class TrustAllTlsTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        // A self-signed cert with no CA — the same trust situation as the Pi.
        val selfSigned = HeldCertificate.Builder()
            .addSubjectAlternativeName("localhost")
            .build()
        val serverCerts = HandshakeCertificates.Builder()
            .heldCertificate(selfSigned)
            .build()
        server = MockWebServer().apply {
            useHttps(serverCerts.sslSocketFactory(), false)
            enqueue(MockResponse().setBody("ok"))
            start()
        }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /**
     * Documents the failure mode: a stock client (system trust manager only —
     * all the app had before trustAllTls()) rejects the self-signed cert.
     * This is the bug the regression guard prevents.
     */
    @Test
    fun defaultClient_rejectsSelfSignedCert() {
        val client = OkHttpClient()
        try {
            client.newCall(Request.Builder().url(server.url("/")).build()).execute()
            fail("Expected a TLS trust failure against the self-signed cert")
        } catch (e: SSLHandshakeException) {
            val chain = generateSequence<Throwable>(e) { it.cause }.toList()
            assertTrue(
                "Expected a cert-path/trust-anchor failure, got: " +
                    chain.joinToString { it.toString() },
                chain.any {
                    it is CertPathValidatorException ||
                        it is CertificateException ||
                        (it.message ?: "").contains("certification path", ignoreCase = true) ||
                        (it.message ?: "").contains("Trust anchor", ignoreCase = true)
                }
            )
        }
    }

    /**
     * The regression guard: a client built exactly as AppModule wires it —
     * via trustAllTls() — connects to the self-signed server.
     */
    @Test
    fun trustAllTls_acceptsSelfSignedCert() {
        val (sslSocketFactory, trustManager) = trustAllTls()
        val client = OkHttpClient.Builder()
            .sslSocketFactory(sslSocketFactory, trustManager)
            .build()
        client.newCall(Request.Builder().url(server.url("/")).build()).execute().use { resp ->
            assertEquals(200, resp.code)
            assertEquals("ok", resp.body!!.string())
        }
    }
}
