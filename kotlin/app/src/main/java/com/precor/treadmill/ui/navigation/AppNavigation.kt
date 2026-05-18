package com.precor.treadmill.ui.navigation

import android.Manifest
import android.content.pm.PackageManager
import android.content.res.Configuration
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.ui.components.*
import com.precor.treadmill.ui.screens.debug.DebugScreen
import com.precor.treadmill.ui.screens.lobby.LobbyScreen
import com.precor.treadmill.ui.screens.profiles.ProfilePickerScreen
import com.precor.treadmill.ui.screens.running.RunningScreen
import com.precor.treadmill.ui.screens.setup.SetupScreen
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import com.precor.treadmill.ui.viewmodel.VoiceState
import com.precor.treadmill.ui.viewmodel.VoiceViewModel
import kotlinx.coroutines.launch
import org.koin.compose.koinInject
import org.koin.androidx.compose.koinViewModel

object Routes {
    const val SETUP = "setup"
    const val PROFILES = "profiles"
    const val LOBBY = "lobby"
    const val RUNNING = "running"
    const val DEBUG = "debug"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppNavigation(
    navController: NavHostController = rememberNavController(),
    serverPreferences: ServerPreferences = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val serverUrl by serverPreferences.serverUrl.collectAsState(initial = null)
    val viewModel: TreadmillViewModel = koinViewModel()
    val voiceViewModel: VoiceViewModel = koinViewModel()
    val wsConnected by viewModel.wsConnected.collectAsState()
    val scope = rememberCoroutineScope()

    // Runtime mic permission handling
    var pendingVoicePrompt by remember { mutableStateOf<String?>(null) }
    val micPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            voiceViewModel.toggle(pendingVoicePrompt)
        }
        pendingVoicePrompt = null
    }

    val handleVoiceToggle: (String?) -> Unit = { prompt ->
        if (voiceViewModel.voiceState.value == VoiceState.IDLE) {
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED
            ) {
                voiceViewModel.toggle(prompt)
            } else {
                pendingVoicePrompt = prompt
                micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        } else {
            voiceViewModel.toggle(prompt)
        }
    }

    // Voice state from ViewModel
    val voiceStateEnum by voiceViewModel.voiceState.collectAsState()

    // Drive always-on Gemini connection lifecycle from server WS state
    LaunchedEffect(wsConnected) {
        if (wsConnected) voiceViewModel.ensureConnected()
        else voiceViewModel.onServerDisconnected()
    }

    // Profile state for nav bar
    val activeProfile by viewModel.activeProfile.collectAsState()
    val guestMode by viewModel.guestMode.collectAsState()

    // Keep voice context updated with treadmill state
    val treadmillStatus by viewModel.status.collectAsState()
    val programState by viewModel.program.collectAsState()
    LaunchedEffect(treadmillStatus, programState) {
        // Build a StatusMessage from current state for voice context
        val statusMsg = com.precor.treadmill.data.remote.models.StatusMessage(
            proxy = treadmillStatus.proxy,
            emulate = treadmillStatus.emulate,
            emuSpeed = treadmillStatus.emuSpeed,
            emuSpeedMph = treadmillStatus.emuSpeed / 10.0,
            emuIncline = treadmillStatus.emuIncline,
            speed = treadmillStatus.speed,
            incline = treadmillStatus.incline?.toDouble(),
            treadmillConnected = treadmillStatus.treadmillConnected,
        )
        val programMsg = com.precor.treadmill.data.remote.models.ProgramMessage(
            program = programState.program,
            running = programState.running,
            paused = programState.paused,
            completed = programState.completed,
            currentInterval = programState.currentInterval,
            intervalElapsed = programState.intervalElapsed,
            totalElapsed = programState.totalElapsed,
            totalDuration = programState.totalDuration,
        )
        voiceViewModel.updateTreadmillState(statusMsg, programMsg)
    }

    // Wire profileChanged to VoiceViewModel so config is invalidated on profile switch
    LaunchedEffect(Unit) {
        viewModel.profileChanged.collect {
            voiceViewModel.onProfileChanged()
        }
    }

    // Wait for DataStore to load before deciding start destination
    val url = serverUrl ?: return

    // On app restart, check if server already has an active profile — skip picker if so
    var checkedActiveProfile by remember { mutableStateOf(false) }
    LaunchedEffect(url) {
        if (url.isNotBlank() && !checkedActiveProfile) {
            viewModel.fetchActiveProfile()
            // Small delay for the network call to complete
            kotlinx.coroutines.delay(500)
            checkedActiveProfile = true
        }
    }

    val startDestination = when {
        url.isBlank() -> Routes.SETUP
        activeProfile != null || guestMode -> Routes.LOBBY
        else -> Routes.PROFILES
    }

    // Current route for tab bar highlighting
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route ?: startDestination

    // Settings sheet
    val settingsSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var showSettings by remember { mutableStateOf(false) }

    // Chat sheet
    val chatSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var showChat by remember { mutableStateOf(false) }

    // Map VoiceState enum to string for existing components
    val voiceState = when (voiceStateEnum) {
        VoiceState.IDLE -> "idle"
        VoiceState.CONNECTING -> "connecting"
        VoiceState.LISTENING -> "listening"
        VoiceState.SPEAKING -> "speaking"
    }

    // Toast
    var toastMsg by remember { mutableStateOf("") }
    var toastVisible by remember { mutableStateOf(false) }
    val showToast: (String) -> Unit = { msg ->
        toastMsg = msg
        toastVisible = true
    }

    // Auto-dismiss toast
    LaunchedEffect(toastVisible) {
        if (toastVisible) {
            kotlinx.coroutines.delay(8000)
            toastVisible = false
        }
    }

    // Also collect toasts from ViewModel
    LaunchedEffect(Unit) {
        viewModel.toast.collect { msg ->
            showToast(msg)
        }
    }

    // Auto-navigate to Running when a program starts (e.g. via voice command from Lobby)
    LaunchedEffect(programState.running) {
        if (programState.running && currentRoute == Routes.LOBBY) {
            navController.navigate(Routes.RUNNING)
        }
    }

    // Orientation detection
    val isLandscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE

    // Navigation callbacks shared between NavRail and content
    val navigateTo: (String) -> Unit = { route ->
        if (route != currentRoute) {
            navController.navigate(route) {
                popUpTo(Routes.LOBBY)
                launchSingleTop = true
            }
        }
    }

    val navRail = @Composable {
        NavRail(
            currentRoute = currentRoute,
            voiceState = voiceState,
            onNavigate = navigateTo,
            onVoiceToggle = { handleVoiceToggle(null) },
            onSettingsToggle = {
                showSettings = true
                scope.launch { settingsSheetState.show() }
            },
            activeProfile = activeProfile,
            guestMode = guestMode,
        )
    }

    val navHostContent = @Composable {
        NavHost(
            navController = navController,
            startDestination = startDestination,
        ) {
            composable(Routes.SETUP) {
                SetupScreen(
                    onConnected = {
                        navController.navigate(Routes.PROFILES) {
                            popUpTo(Routes.SETUP) { inclusive = true }
                        }
                    }
                )
            }
            composable(Routes.PROFILES) {
                ProfilePickerScreen(
                    onProfileSelected = {
                        navController.navigate(Routes.LOBBY) {
                            popUpTo(Routes.PROFILES) { inclusive = true }
                        }
                    },
                    viewModel = viewModel,
                )
            }
            composable(Routes.LOBBY) {
                LobbyScreen(
                    onNavigateToRun = {
                        navController.navigate(Routes.RUNNING)
                    },
                    viewModel = viewModel,
                )
            }
            composable(Routes.RUNNING) {
                RunningScreen(
                    viewModel = viewModel,
                    onVoiceToggle = handleVoiceToggle,
                )
            }
            composable(Routes.DEBUG) {
                DebugScreen(viewModel = viewModel)
            }
        }
    }

    val showChrome = currentRoute != Routes.SETUP && currentRoute != Routes.PROFILES

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg),
    ) {
        if (isLandscape) {
            Column(modifier = Modifier.fillMaxSize()) {
                if (showChrome) {
                    DisconnectBanner(connected = wsConnected)
                }
                Row(modifier = Modifier.weight(1f)) {
                    if (showChrome) navRail()
                    Box(modifier = Modifier.weight(1f)) {
                        navHostContent()
                    }
                }
            }
        } else {
            Column(modifier = Modifier.fillMaxSize()) {
                if (showChrome) {
                    DisconnectBanner(connected = wsConnected)
                }
                Box(modifier = Modifier.weight(1f)) {
                    navHostContent()
                }
                if (showChrome) navRail()
            }
        }

        // Toast overlay
        ToastBanner(
            message = toastMsg,
            visible = toastVisible,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .statusBarsPadding(),
        )
    }

    // Settings bottom sheet
    if (showSettings) {
        SettingsSheet(
            sheetState = settingsSheetState,
            onDismiss = { showSettings = false },
            onNavigateToDebug = {
                navController.navigate(Routes.DEBUG) {
                    popUpTo(Routes.LOBBY)
                    launchSingleTop = true
                }
            },
            onToast = showToast,
            viewModel = viewModel,
        )
    }

    // Chat bottom sheet
    if (showChat) {
        ChatSheet(
            sheetState = chatSheetState,
            onDismiss = { showChat = false },
            onToast = showToast,
        )
    }
}
