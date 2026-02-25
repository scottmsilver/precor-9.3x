package com.precor.treadmill

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.precor.treadmill.ui.navigation.AppNavigation
import com.precor.treadmill.ui.theme.PrecorTreadmillTheme
import com.precor.treadmill.ui.viewmodel.VoiceViewModel
import org.koin.androidx.viewmodel.ext.android.viewModel

class MainActivity : ComponentActivity() {
    companion object {
        private const val TAG = "MainActivity"
        const val ACTION_VOICE_TEST = "com.precor.treadmill.VOICE_TEST"
        const val ACTION_VOICE_TOGGLE = "com.precor.treadmill.VOICE_TOGGLE"
    }

    private val voiceViewModel: VoiceViewModel by viewModel()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // Hide system navigation bar — sticky immersive so it auto-hides after swipe
        val insetsController = WindowCompat.getInsetsController(window, window.decorView)
        insetsController.hide(WindowInsetsCompat.Type.navigationBars())
        insetsController.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE

        setContent {
            PrecorTreadmillTheme {
                AppNavigation()
            }
        }

        handleVoiceTestIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleVoiceTestIntent(intent)
    }

    private fun handleVoiceTestIntent(intent: Intent) {
        when (intent.action) {
            ACTION_VOICE_TEST -> {
                val cmd = intent.getStringExtra("cmd") ?: return
                Log.d(TAG, "Voice test command: $cmd")
                voiceViewModel.sendTestCommand(cmd)
            }
            ACTION_VOICE_TOGGLE -> {
                Log.d(TAG, "Voice toggle (mic mode)")
                voiceViewModel.toggle()
            }
            else -> return
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            // Re-hide nav bar when focus returns (e.g. after dialog or app switch)
            val insetsController = WindowCompat.getInsetsController(window, window.decorView)
            insetsController.hide(WindowInsetsCompat.Type.navigationBars())
            insetsController.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }
}
