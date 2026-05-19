package com.precor.treadmill.di

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.TreadmillWebSocket
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import com.precor.treadmill.ui.viewmodel.VoiceViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import org.koin.android.ext.koin.androidContext
import org.koin.core.module.dsl.viewModel
import org.koin.dsl.module
import retrofit2.Retrofit
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

val appModule = module {

    single { ServerPreferences(androidContext()) }

    single {
        Json {
            ignoreUnknownKeys = true   // don't crash on new server fields
            isLenient = true           // tolerate unquoted strings, trailing commas
            coerceInputValues = true   // null → default value for non-nullable fields
            explicitNulls = false      // omit nulls in output, tolerate missing fields
        }
    }

    single {
        // The treadmill is a personal LAN appliance serving a per-device
        // self-signed cert (generated on the Pi at setup time). There is no CA
        // to anchor to, and the cert SAN won't match an arbitrary IP/host, so
        // we trust the cert unconditionally and skip hostname verification.
        // Mirrors the iOS client (TrustAllDelegate) and the app's
        // cleartext-allowed posture; acceptable for a non-internet-facing box.
        val (sslSocketFactory, trustManager) = trustAllTls()
        OkHttpClient.Builder()
            .sslSocketFactory(sslSocketFactory, trustManager)
            .hostnameVerifier { _, _ -> true }
            .addInterceptor(DynamicBaseUrlInterceptor(get()))
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    single {
        val json: Json = get()
        Retrofit.Builder()
            .baseUrl("http://placeholder.invalid/")
            .client(get())
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
    }

    single { get<Retrofit>().create(TreadmillApi::class.java) }

    single { TreadmillWebSocket(get(), get()) }

    viewModel { TreadmillViewModel(get(), get(), get()) }
    viewModel { VoiceViewModel(get(), get()) }
}

/**
 * Builds an [SSLSocketFactory] + [X509TrustManager] pair that accept any
 * server certificate. The treadmill serves a per-device self-signed cert with
 * no CA to anchor to; see the OkHttpClient provider for the threat-model
 * rationale. Extracted as a named unit so the trust behavior can be
 * unit-tested against a self-signed MockWebServer.
 */
private fun trustAllTls(): Pair<SSLSocketFactory, X509TrustManager> {
    val trustManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }
    val sslContext = SSLContext.getInstance("TLS")
    sslContext.init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
    return sslContext.socketFactory to trustManager
}

/**
 * Interceptor that replaces the placeholder base URL with the actual server URL
 * from DataStore preferences on each request.
 *
 * Uses a cached @Volatile field updated by a coroutine collector instead of
 * runBlocking on every request, which would block OkHttp dispatcher threads.
 */
private class DynamicBaseUrlInterceptor(
    serverPreferences: ServerPreferences,
) : Interceptor {
    @Volatile
    private var cachedUrl: String = runBlocking { serverPreferences.serverUrl.first() }

    init {
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            serverPreferences.serverUrl.collect { url ->
                cachedUrl = url
            }
        }
    }

    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val serverUrl = cachedUrl

        if (serverUrl.isBlank()) {
            return chain.proceed(original)
        }

        val baseUrl = serverUrl.trimEnd('/')
        val newUrl = original.url.toString().replace(
            "http://placeholder.invalid/",
            "$baseUrl/"
        )

        val newRequest = original.newBuilder()
            .url(newUrl)
            .build()

        return chain.proceed(newRequest)
    }
}
