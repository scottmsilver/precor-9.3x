package com.precor.treadmill.data.preferences

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "server_prefs")

class ServerPreferences(private val context: Context) {

    companion object {
        private val KEY_SERVER_URL = stringPreferencesKey("server_url")
        private val KEY_SMARTASS_MODE = booleanPreferencesKey("smartass_mode")
    }

    val serverUrl: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[KEY_SERVER_URL] ?: ""
    }

    val smartassMode: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[KEY_SMARTASS_MODE] ?: false
    }

    suspend fun setServerUrl(url: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_SERVER_URL] = url
        }
    }

    suspend fun setSmartassMode(enabled: Boolean) {
        context.dataStore.edit { prefs ->
            prefs[KEY_SMARTASS_MODE] = enabled
        }
    }
}
