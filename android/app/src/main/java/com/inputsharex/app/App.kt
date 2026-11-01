package com.inputsharex.app

import android.app.Application
import com.inputsharex.app.data.SettingsStore

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        SettingsStore.init(this)
    }
}
