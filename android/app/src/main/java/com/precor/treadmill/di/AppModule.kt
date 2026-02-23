package com.precor.treadmill.di

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.TreadmillWebSocket
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import com.precor.treadmill.ui.viewmodel.VoiceViewModel
import kotlinx.coroutines.flow.first
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
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

val appModule = module {

    single { ServerPreferences(androidContext()) }

    single {
        Json {
            ignoreUnknownKeys = true
            isLenient = true
        }
    }

    single {
        // Trust all certs for self-signed Tailscale certs on the Pi
        val trustManager = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<TrustManager>(trustManager), null)
        }

        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
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
    viewModel { VoiceViewModel(get()) }
}

/**
 * Interceptor that replaces the placeholder base URL with the actual server URL
 * from DataStore preferences on each request.
 */
private class DynamicBaseUrlInterceptor(
    private val serverPreferences: ServerPreferences,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val serverUrl = runBlocking { serverPreferences.serverUrl.first() }

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
