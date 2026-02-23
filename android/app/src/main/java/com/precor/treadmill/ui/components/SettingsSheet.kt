package com.precor.treadmill.ui.components

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.koin.compose.koinInject
import org.koin.androidx.compose.koinViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    sheetState: SheetState,
    onDismiss: () -> Unit,
    onNavigateToDebug: () -> Unit,
    onToast: (String) -> Unit,
    viewModel: TreadmillViewModel = koinViewModel(),
    serverPreferences: ServerPreferences = koinInject(),
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val status by viewModel.status.collectAsState()

    var smartass by remember { mutableStateOf(false) }
    var debugUnlocked by remember { mutableStateOf(false) }
    var debugTaps by remember { mutableStateOf(listOf<Long>()) }

    LaunchedEffect(Unit) {
        smartass = serverPreferences.smartassMode.first()
    }

    // Reset debug when sheet closes
    LaunchedEffect(sheetState.isVisible) {
        if (!sheetState.isVisible) debugUnlocked = false
    }

    val gpxLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.readBytes() ?: return@launch
                val requestBody = bytes.toRequestBody("application/gpx+xml".toMediaType())
                val part = MultipartBody.Part.createFormData("file", "route.gpx", requestBody)
                val res = api.uploadGpx(part)
                if (res.ok && res.program != null) {
                    val name = res.program.name.ifBlank { "Route" }
                    onToast("Loaded GPX route \"$name\". Tap Start to begin!")
                    haptic(context, 25)
                    onDismiss()
                } else {
                    onToast("GPX upload failed: ${res.error ?: "unknown error"}")
                }
            }.onFailure { e ->
                onToast("GPX upload failed: ${e.message ?: "unknown error"}")
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = colors.card,
        contentColor = colors.text,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 32.dp),
        ) {
            // Header with triple-tap debug unlock
            Text(
                text = "Settings",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = colors.text,
                modifier = Modifier
                    .clickable {
                        val now = System.currentTimeMillis()
                        debugTaps = (debugTaps + now).filter { now - it < 500 }
                        if (debugTaps.size >= 3) {
                            debugTaps = emptyList()
                            debugUnlocked = true
                            haptic(context, 50)
                        }
                    }
                    .padding(bottom = 20.dp),
            )

            // Import GPX
            SettingsRow(
                label = "Import GPX Route",
                onClick = { gpxLauncher.launch("*/*") },
            )

            // Debug Console
            SettingsRow(
                label = "Debug Console",
                onClick = {
                    onNavigateToDebug()
                    haptic(context, 25)
                    onDismiss()
                },
            )

            // Smart-ass mode toggle
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clickable {
                        val next = !smartass
                        smartass = next
                        scope.launch { serverPreferences.setSmartassMode(next) }
                        haptic(context, 25)
                        onToast(if (next) "Smart-ass mode ON. Brace yourself." else "Smart-ass mode off.")
                    }
                    .padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Smart-ass Mode",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                Switch(
                    checked = smartass,
                    onCheckedChange = null,
                    colors = SwitchDefaults.colors(
                        checkedTrackColor = colors.purple,
                        uncheckedTrackColor = colors.fill,
                        checkedThumbColor = colors.text,
                        uncheckedThumbColor = colors.text3,
                    ),
                )
            }

            HorizontalDivider(color = colors.separator, thickness = 0.5.dp)

            // Heart Rate Monitor section
            HrmSection(viewModel = viewModel, onToast = onToast)

            // Debug mode toggle (unlocked by triple-tap)
            if (debugUnlocked) {
                Spacer(Modifier.height(24.dp))
                Text(
                    text = "MODE",
                    color = colors.text3,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.3.sp,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(colors.fill2),
                ) {
                    listOf("proxy" to status.proxy, "emulate" to status.emulate).forEach { (mode, active) ->
                        val bg = if (active) {
                            if (mode == "proxy") colors.green else colors.purple
                        } else colors.fill2
                        val fg = if (active) {
                            if (mode == "proxy") colors.bg else colors.text
                        } else colors.text3

                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .height(44.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(bg)
                                .clickable {
                                    viewModel.setMode(mode)
                                    haptic(context, longArrayOf(25, 30, 25))
                                },
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = mode.replaceFirstChar { it.uppercase() },
                                color = fg,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsRow(
    label: String,
    onClick: () -> Unit,
) {
    val colors = LocalPrecorColors.current
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(48.dp)
            .clickable(onClick = onClick)
            .padding(horizontal = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = label,
            color = colors.text,
            fontSize = 15.sp,
        )
        Text(
            text = "\u203A",
            color = colors.text3,
            fontSize = 13.sp,
        )
    }
    HorizontalDivider(color = colors.separator, thickness = 0.5.dp)
}

@Composable
private fun HrmSection(
    viewModel: TreadmillViewModel,
    onToast: (String) -> Unit,
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val status by viewModel.status.collectAsState()
    val hrmDevices by viewModel.hrmDevices.collectAsState()

    Spacer(Modifier.height(20.dp))
    Text(
        text = "HEART RATE MONITOR",
        color = colors.text3,
        fontSize = 13.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.3.sp,
        modifier = Modifier.padding(bottom = 8.dp),
    )

    if (status.hrmConnected) {
        // Connected state — show device + BPM
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    color = colors.fill,
                    shape = RoundedCornerShape(10.dp),
                )
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(
                    text = status.hrmDevice.ifBlank { "Connected" },
                    color = colors.text,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = "Connected",
                    color = colors.green,
                    fontSize = 12.sp,
                )
            }
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                val hrColor = when {
                    status.heartRate >= 170 -> Color(0xFFC45C52)
                    status.heartRate >= 150 -> Color(0xFFD4845A)
                    status.heartRate >= 120 -> Color(0xFFD4B85A)
                    else -> Color(0xFF6BC89B)
                }
                Text(
                    text = "\u2665",
                    color = hrColor,
                    fontSize = 16.sp,
                )
                Text(
                    text = if (status.heartRate > 0) "${status.heartRate}" else "---",
                    color = hrColor,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = "bpm",
                    color = colors.text3,
                    fontSize = 12.sp,
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        // Forget button
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(44.dp)
                .clickable {
                    viewModel.forgetHrmDevice()
                    haptic(context, 25)
                    onToast("HRM device forgotten")
                }
                .padding(horizontal = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(text = "Forget Device", color = colors.red, fontSize = 15.sp)
        }
    } else if (hrmDevices.isNotEmpty()) {
        // Scan results — show device list
        Text(
            text = "Select a device:",
            color = colors.text2,
            fontSize = 13.sp,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        hrmDevices.forEach { device ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        color = colors.fill,
                        shape = RoundedCornerShape(10.dp),
                    )
                    .clickable {
                        viewModel.selectHrmDevice(device.address)
                        haptic(context, 25)
                        onToast("Connecting to ${device.name}...")
                    }
                    .padding(12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        text = device.name.ifBlank { device.address },
                        color = colors.text,
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = device.address,
                        color = colors.text3,
                        fontSize = 11.sp,
                    )
                }
                // RSSI signal indicator
                Text(
                    text = "${device.rssi} dBm",
                    color = colors.text3,
                    fontSize = 12.sp,
                )
            }
            Spacer(Modifier.height(4.dp))
        }
    } else {
        // Not connected, no devices — show scan button
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    color = colors.fill,
                    shape = RoundedCornerShape(10.dp),
                )
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "No HRM connected",
                color = colors.text3,
                fontSize = 15.sp,
            )
        }
    }

    Spacer(Modifier.height(4.dp))
    if (!status.hrmConnected) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(44.dp)
                .clickable {
                    viewModel.scanHrmDevices()
                    haptic(context, 25)
                    onToast("Scanning for HRM devices...")
                }
                .padding(horizontal = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(text = "Scan for Devices", color = colors.teal, fontSize = 15.sp)
        }
    }

    HorizontalDivider(color = colors.separator, thickness = 0.5.dp)
}
