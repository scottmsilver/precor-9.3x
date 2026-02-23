package com.precor.treadmill

import android.app.Application
import com.precor.treadmill.di.appModule
import org.koin.android.ext.koin.androidContext
import org.koin.core.context.startKoin

class TreadmillApp : Application() {
    override fun onCreate() {
        super.onCreate()
        startKoin {
            androidContext(this@TreadmillApp)
            modules(appModule)
        }
    }
}
