package com.precor.treadmill.ui.navigation

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.ui.components.*
import com.precor.treadmill.ui.screens.debug.DebugScreen
import com.precor.treadmill.ui.screens.lobby.LobbyScreen
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
    val serverUrl by serverPreferences.serverUrl.collectAsState(initial = null)
    val viewModel: TreadmillViewModel = koinViewModel()
    val voiceViewModel: VoiceViewModel = koinViewModel()
    val wsConnected by viewModel.wsConnected.collectAsState()
    val scope = rememberCoroutineScope()

    // Voice state from ViewModel
    val voiceStateEnum by voiceViewModel.voiceState.collectAsState()

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

    // Wait for DataStore to load before deciding start destination
    val url = serverUrl ?: return

    val startDestination = if (url.isBlank()) Routes.SETUP else Routes.LOBBY

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

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg),
    ) {
        Column(modifier = Modifier.fillMaxSize()) {
            // Disconnect banner (only shown after setup)
            if (currentRoute != Routes.SETUP) {
                DisconnectBanner(connected = wsConnected)
            }

            // Nav host content
            Box(modifier = Modifier.weight(1f)) {
                NavHost(
                    navController = navController,
                    startDestination = startDestination,
                ) {
                    composable(Routes.SETUP) {
                        SetupScreen(
                            onConnected = {
                                navController.navigate(Routes.LOBBY) {
                                    popUpTo(Routes.SETUP) { inclusive = true }
                                }
                            }
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
                            voiceState = voiceState,
                            onNavigateHome = {
                                navController.popBackStack(Routes.LOBBY, inclusive = false)
                            },
                            onVoiceToggle = { prompt ->
                                voiceViewModel.toggle(prompt)
                            },
                        )
                    }
                    composable(Routes.DEBUG) {
                        DebugScreen(viewModel = viewModel)
                    }
                }
            }

            // Tab bar (hidden on setup and running screens)
            if (currentRoute != Routes.SETUP && currentRoute != Routes.RUNNING) {
                TabBar(
                    currentRoute = currentRoute,
                    voiceState = voiceState,
                    onNavigate = { route ->
                        if (route != currentRoute) {
                            navController.navigate(route) {
                                popUpTo(Routes.LOBBY)
                                launchSingleTop = true
                            }
                        }
                    },
                    onVoiceToggle = {
                        voiceViewModel.toggle()
                    },
                    onSettingsToggle = {
                        showSettings = true
                        scope.launch { settingsSheetState.show() }
                    },
                )
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

        // Voice overlay
        VoiceOverlay(
            voiceState = voiceState,
            modifier = Modifier.align(Alignment.TopCenter),
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
