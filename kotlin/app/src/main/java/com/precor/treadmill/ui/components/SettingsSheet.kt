package com.precor.treadmill.ui.components

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.ui.res.painterResource
import com.precor.treadmill.ui.theme.Backgrounds
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.Profile
import com.precor.treadmill.data.remote.models.UpdateUserRequest
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.koin.compose.koinInject
import org.koin.androidx.compose.koinViewModel
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    sheetState: SheetState,
    onDismiss: () -> Unit,
    onNavigateToDebug: () -> Unit,
    onToast: (String) -> Unit,
    voiceInputEnabled: Boolean,
    microphonePermissionGranted: Boolean,
    onVoiceInputEnabledChange: (Boolean) -> Unit,
    onRequestMicrophonePermission: () -> Unit,
    viewModel: TreadmillViewModel = koinViewModel(),
    serverPreferences: ServerPreferences = koinInject(),
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val status by viewModel.status.collectAsState()

    var smartass by remember { mutableStateOf(false) }
    var weightText by remember { mutableStateOf("") }
    var vestText by remember { mutableStateOf("") }
    var debugUnlocked by remember { mutableStateOf(false) }
    var debugTaps by remember { mutableStateOf(listOf<Long>()) }

    LaunchedEffect(Unit) {
        smartass = serverPreferences.smartassMode.first()
        runCatching {
            val user = api.getUser()
            weightText = user.weightLbs.toString()
            vestText = if (user.vestLbs > 0) user.vestLbs.toString() else ""
        }
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

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clickable { onVoiceInputEnabledChange(!voiceInputEnabled) }
                    .padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Voice Input",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                Switch(
                    checked = voiceInputEnabled,
                    onCheckedChange = null,
                    colors = SwitchDefaults.colors(
                        checkedTrackColor = colors.purple,
                        uncheckedTrackColor = colors.fill,
                        checkedThumbColor = colors.text,
                        uncheckedThumbColor = colors.text3,
                    ),
                )
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Microphone Permission",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                if (microphonePermissionGranted) {
                    Text(
                        text = "Granted",
                        color = colors.green,
                        fontSize = 13.sp,
                    )
                } else {
                    TextButton(onClick = onRequestMicrophonePermission) {
                        Text("Not granted · Grant")
                    }
                }
            }

            // Running-screen background photo
            val bgKey by serverPreferences.backgroundImage.collectAsState(initial = "ridge")
            Column(modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 6.dp)) {
                Text(
                    text = "Background",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Backgrounds.all.forEach { bg ->
                        val selected = bg.key == bgKey
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Image(
                                painter = painterResource(bg.res),
                                contentDescription = bg.label,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier
                                    .size(width = 92.dp, height = 56.dp)
                                    .clip(RoundedCornerShape(8.dp))
                                    .then(
                                        if (selected) Modifier.border(2.dp, colors.purple, RoundedCornerShape(8.dp))
                                        else Modifier.border(1.dp, colors.fill, RoundedCornerShape(8.dp))
                                    )
                                    .clickable {
                                        scope.launch { serverPreferences.setBackgroundImage(bg.key) }
                                        haptic(context, 25)
                                    },
                            )
                            Text(
                                text = bg.label,
                                color = if (selected) colors.text else colors.text3,
                                fontSize = 11.sp,
                                modifier = Modifier.padding(top = 3.dp),
                            )
                        }
                    }
                }
            }

            // Weight
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Weight",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    androidx.compose.foundation.text.BasicTextField(
                        value = weightText,
                        onValueChange = { v ->
                            weightText = v.filter { it.isDigit() }.take(3)
                        },
                        singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Number,
                        ),
                        textStyle = androidx.compose.ui.text.TextStyle(
                            color = colors.text,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold,
                            textAlign = androidx.compose.ui.text.style.TextAlign.End,
                        ),
                        modifier = Modifier
                            .width(60.dp)
                            .height(32.dp)
                            .background(colors.fill2, RoundedCornerShape(8.dp))
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(
                            onDone = {
                                val lbs = weightText.toIntOrNull()
                                if (lbs != null && lbs in 50..500) {
                                    scope.launch {
                                        runCatching { api.updateUser(UpdateUserRequest(weightLbs = lbs)) }
                                    }
                                }
                            },
                        ),
                    )
                    Text(
                        text = "lbs",
                        color = colors.text3,
                        fontSize = 13.sp,
                    )
                }
            }

            // Vest weight
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Weight Vest",
                    color = colors.text,
                    fontSize = 15.sp,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    androidx.compose.foundation.text.BasicTextField(
                        value = vestText,
                        onValueChange = { v ->
                            vestText = v.filter { it.isDigit() }.take(3)
                        },
                        singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Number,
                        ),
                        textStyle = androidx.compose.ui.text.TextStyle(
                            color = colors.text,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold,
                            textAlign = androidx.compose.ui.text.style.TextAlign.End,
                        ),
                        modifier = Modifier
                            .width(60.dp)
                            .background(colors.fill2, RoundedCornerShape(8.dp))
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(
                            onDone = {
                                val lbs = vestText.toIntOrNull() ?: 0
                                if (lbs in 0..100) {
                                    scope.launch {
                                        runCatching { api.updateUser(UpdateUserRequest(vestLbs = lbs)) }
                                    }
                                }
                            },
                        ),
                        decorationBox = { inner ->
                            if (vestText.isEmpty()) {
                                Text("0", color = colors.text3, fontSize = 15.sp,
                                    textAlign = androidx.compose.ui.text.style.TextAlign.End,
                                    modifier = Modifier.fillMaxWidth())
                            }
                            inner()
                        },
                    )
                    Text(
                        text = "lbs",
                        color = colors.text3,
                        fontSize = 13.sp,
                    )
                }
            }

            HorizontalDivider(color = colors.separator, thickness = 0.5.dp)

            // Profile section
            ProfileSection(viewModel = viewModel, onToast = onToast)

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

/** Avatar colors from the warm muted palette. */
private val AVATAR_COLORS = listOf(
    "#d4c4a8", // tan
    "#b8c9d4", // blue-gray
    "#c9b8b0", // rose
    "#b0c9b8", // sage
    "#c4b8d4", // lavender
)

private fun parseHexColor(hex: String): Color {
    return try {
        Color(android.graphics.Color.parseColor(hex))
    } catch (_: Exception) {
        Color(android.graphics.Color.parseColor("#d4c4a8"))
    }
}

/**
 * Loads a bitmap from a URL on a background thread.
 * Returns null on failure.
 */
@Composable
private fun rememberAvatarBitmap(avatarUrl: String?): Bitmap? {
    var bitmap by remember(avatarUrl) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(avatarUrl) {
        if (avatarUrl.isNullOrBlank()) {
            bitmap = null
            return@LaunchedEffect
        }
        bitmap = withContext(Dispatchers.IO) {
            try {
                val conn = URL(avatarUrl).openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                conn.useCaches = true
                conn.inputStream.use { BitmapFactory.decodeStream(it) }
            } catch (_: Exception) {
                null
            }
        }
    }
    return bitmap
}

@Composable
private fun ProfileSection(
    viewModel: TreadmillViewModel,
    onToast: (String) -> Unit,
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val activeProfile by viewModel.activeProfile.collectAsState()
    val guestMode by viewModel.guestMode.collectAsState()
    val serverPreferences: ServerPreferences = koinInject()
    val serverUrl by serverPreferences.serverUrl.collectAsState(initial = "")

    var editingName by remember { mutableStateOf(false) }
    var nameText by remember { mutableStateOf("") }
    var confirmingDelete by remember { mutableStateOf(false) }
    // Bump this to force avatar reload after upload/delete
    var avatarVersion by remember { mutableStateOf(0) }

    LaunchedEffect(Unit) {
        viewModel.fetchActiveProfile()
    }

    // Photo picker launcher
    val photoPickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        val profile = activeProfile ?: return@rememberLauncherForActivityResult
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.readBytes()
                    ?: return@launch
                viewModel.uploadAvatar(
                    id = profile.id,
                    imageBytes = bytes,
                    onSuccess = {
                        avatarVersion++
                        onToast("Avatar updated")
                        haptic(context, 25)
                    },
                    onError = { err -> onToast("Upload failed: $err") },
                )
            }.onFailure { e ->
                onToast("Failed to read image: ${e.message}")
            }
        }
    }

    // Camera launcher
    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicturePreview()
    ) { bitmap: Bitmap? ->
        val profile = activeProfile ?: return@rememberLauncherForActivityResult
        if (bitmap == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val stream = ByteArrayOutputStream()
                bitmap.compress(Bitmap.CompressFormat.JPEG, 85, stream)
                val bytes = stream.toByteArray()
                viewModel.uploadAvatar(
                    id = profile.id,
                    imageBytes = bytes,
                    onSuccess = {
                        avatarVersion++
                        onToast("Avatar updated")
                        haptic(context, 25)
                    },
                    onError = { err -> onToast("Upload failed: $err") },
                )
            }.onFailure { e ->
                onToast("Failed to capture photo: ${e.message}")
            }
        }
    }

    Spacer(Modifier.height(20.dp))
    Text(
        text = "PROFILE",
        color = colors.text3,
        fontSize = 13.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.3.sp,
        modifier = Modifier.padding(bottom = 8.dp),
    )

    val profile = activeProfile
    val profileColor = profile?.color?.let {
        try { Color(android.graphics.Color.parseColor(it)) } catch (_: Exception) { null }
    }

    if (profile != null) {
        val avatarUrl = if (profile.hasAvatar && serverUrl.isNotBlank()) {
            "${serverUrl.trimEnd('/')}/api/profiles/${profile.id}/avatar?v=$avatarVersion"
        } else null
        val avatarBitmap = rememberAvatarBitmap(avatarUrl)

        // Avatar + editable name row
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    color = colors.fill,
                    shape = RoundedCornerShape(10.dp),
                )
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .clip(CircleShape)
                    .background(profileColor ?: colors.fill2),
                contentAlignment = Alignment.Center,
            ) {
                if (avatarBitmap != null) {
                    Image(
                        bitmap = avatarBitmap.asImageBitmap(),
                        contentDescription = profile.name,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    Text(
                        text = profile.initials.ifBlank { profile.name.take(1).uppercase() },
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = if (profileColor != null) Color(0xFF1E1D1B) else colors.text3,
                    )
                }
            }
            if (editingName) {
                androidx.compose.foundation.text.BasicTextField(
                    value = nameText,
                    onValueChange = { nameText = it.take(50) },
                    singleLine = true,
                    textStyle = androidx.compose.ui.text.TextStyle(
                        color = colors.text,
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                    ),
                    modifier = Modifier
                        .weight(1f)
                        .height(32.dp)
                        .background(colors.fill2, RoundedCornerShape(8.dp))
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                    keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                        imeAction = androidx.compose.ui.text.input.ImeAction.Done,
                    ),
                    keyboardActions = androidx.compose.foundation.text.KeyboardActions(
                        onDone = {
                            val trimmed = nameText.trim()
                            if (trimmed.isNotBlank() && trimmed != profile.name) {
                                viewModel.renameProfile(
                                    id = profile.id,
                                    name = trimmed,
                                    onSuccess = { onToast("Renamed to \"$trimmed\"") },
                                    onError = { onToast("Rename failed") },
                                )
                            }
                            editingName = false
                        },
                    ),
                )
            } else {
                Text(
                    text = profile.name,
                    color = colors.text,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier
                        .weight(1f)
                        .clickable {
                            nameText = profile.name
                            editingName = true
                            haptic(context, 10)
                        },
                )
                Text(
                    text = "tap to rename",
                    color = colors.text3,
                    fontSize = 12.sp,
                )
            }
        }

        Spacer(Modifier.height(12.dp))

        // --- Avatar management section ---
        Text(
            text = "AVATAR",
            color = colors.text3,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.3.sp,
            modifier = Modifier.padding(bottom = 8.dp),
        )

        // Upload image / Take photo row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Upload image button
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(colors.fill)
                    .clickable {
                        haptic(context, 15)
                        photoPickerLauncher.launch("image/*")
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "Upload Image",
                    color = colors.teal,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }

            // Take photo button
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(colors.fill)
                    .clickable {
                        haptic(context, 15)
                        cameraLauncher.launch(null)
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "Take Photo",
                    color = colors.teal,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }

        Spacer(Modifier.height(12.dp))

        // Color picker row
        Text(
            text = "COLOR",
            color = colors.text3,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.3.sp,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            AVATAR_COLORS.forEach { hex ->
                val swatchColor = parseHexColor(hex)
                val isSelected = profile.color == hex
                Box(
                    modifier = Modifier
                        .size(32.dp)
                        .clip(CircleShape)
                        .background(swatchColor)
                        .then(
                            if (isSelected) {
                                Modifier.border(3.dp, colors.green, CircleShape)
                            } else {
                                Modifier
                            }
                        )
                        .clickable {
                            haptic(context, 15)
                            viewModel.updateProfileColor(
                                id = profile.id,
                                color = hex,
                                onSuccess = { onToast("Color updated") },
                                onError = { onToast("Failed to update color") },
                            )
                        },
                )
            }
        }

        // Remove avatar (only if has_avatar)
        if (profile.hasAvatar) {
            Spacer(Modifier.height(4.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clickable {
                        haptic(context, 15)
                        viewModel.deleteAvatar(
                            id = profile.id,
                            onSuccess = {
                                avatarVersion++
                                onToast("Photo removed")
                            },
                            onError = { err -> onToast("Failed: $err") },
                        )
                    }
                    .padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Remove Photo",
                    color = colors.red,
                    fontSize = 14.sp,
                )
            }
        }

        Spacer(Modifier.height(4.dp))

        // Delete profile
        if (confirmingDelete) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .padding(horizontal = 4.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Delete \u201c${profile.name}\u201d?",
                    color = colors.text3,
                    fontSize = 14.sp,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        text = "Delete",
                        color = colors.red,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier
                            .clickable {
                                haptic(context, longArrayOf(25, 30, 25))
                                viewModel.deleteProfile(
                                    id = profile.id,
                                    onSuccess = {
                                        onToast("Deleted ${profile.name}")
                                        confirmingDelete = false
                                    },
                                    onError = { err ->
                                        onToast(err)
                                        confirmingDelete = false
                                    },
                                )
                            }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                    Text(
                        text = "Cancel",
                        color = colors.text3,
                        fontSize = 13.sp,
                        modifier = Modifier
                            .clickable {
                                confirmingDelete = false
                                haptic(context, 10)
                            }
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
        } else {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clickable {
                        confirmingDelete = true
                        haptic(context, 15)
                    }
                    .padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Delete Profile",
                    color = colors.red,
                    fontSize = 14.sp,
                )
            }
        }
    } else if (guestMode) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(
                    color = colors.fill,
                    shape = RoundedCornerShape(10.dp),
                )
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Running as Guest",
                color = colors.text3,
                fontSize = 15.sp,
            )
        }
    }

    Spacer(Modifier.height(4.dp))
}
