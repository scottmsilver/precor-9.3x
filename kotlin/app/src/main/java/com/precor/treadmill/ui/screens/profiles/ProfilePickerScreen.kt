package com.precor.treadmill.ui.screens.profiles

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.data.remote.models.Profile
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.koin.androidx.compose.koinViewModel
import org.koin.compose.koinInject
import java.net.HttpURLConnection
import java.net.URL

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

@Composable
fun ProfilePickerScreen(
    onProfileSelected: () -> Unit,
    viewModel: TreadmillViewModel = koinViewModel(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val profiles by viewModel.profiles.collectAsState()
    var showAddDialog by remember { mutableStateOf(false) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    // Confirmation dialog for switching during active session
    var pendingSwitch by remember { mutableStateOf<(() -> Unit)?>(null) }

    LaunchedEffect(Unit) {
        viewModel.fetchProfiles()
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg)
            .systemBarsPadding(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // Title
            Spacer(Modifier.height(24.dp))
            Text(
                text = "Who's running today?",
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = colors.text,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(32.dp))

            // Error message
            errorMsg?.let { msg ->
                Text(
                    text = msg,
                    color = colors.red,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(bottom = 12.dp, start = 24.dp, end = 24.dp),
                )
            }

            // Build the items list: profiles + Guest + Add Profile
            val allItems = buildList {
                profiles.forEach { add(PickerItem.UserProfile(it)) }
                add(PickerItem.Guest)
                add(PickerItem.AddProfile)
            }

            // For few profiles (1-3 user profiles + 2 special = 3-5 items), center them.
            // For more, use start alignment with clip at right edge.
            val shouldCenter = allItems.size <= 4

            LazyRow(
                state = rememberLazyListState(),
                contentPadding = if (shouldCenter) {
                    PaddingValues(horizontal = 24.dp)
                } else {
                    PaddingValues(start = 24.dp, end = 0.dp)
                },
                horizontalArrangement = if (shouldCenter) {
                    Arrangement.spacedBy(24.dp, Alignment.CenterHorizontally)
                } else {
                    Arrangement.spacedBy(24.dp)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                items(allItems, key = { it.key }) { item ->
                    when (item) {
                        is PickerItem.UserProfile -> {
                            ProfileAvatar(
                                profile = item.profile,
                                color = parseHexColor(item.profile.color),
                                enabled = !loading,
                                onClick = {
                                    loading = true
                                    errorMsg = null
                                    haptic(context, 25)
                                    viewModel.selectProfile(
                                        id = item.profile.id,
                                        onSuccess = {
                                            loading = false
                                            onProfileSelected()
                                        },
                                        onError = { err ->
                                            loading = false
                                            if (err.contains("active session", ignoreCase = true) || err.contains("409", ignoreCase = true)) {
                                                pendingSwitch = {
                                                    loading = true
                                                    viewModel.stopAndReset {
                                                        viewModel.selectProfile(
                                                            id = item.profile.id,
                                                            onSuccess = { loading = false; onProfileSelected() },
                                                            onError = { loading = false; errorMsg = it },
                                                        )
                                                    }
                                                }
                                            } else {
                                                errorMsg = err
                                            }
                                        },
                                    )
                                },
                            )
                        }
                        is PickerItem.Guest -> {
                            GuestAvatar(
                                enabled = !loading,
                                onClick = {
                                    loading = true
                                    errorMsg = null
                                    haptic(context, 25)
                                    viewModel.startGuest(
                                        onSuccess = {
                                            loading = false
                                            onProfileSelected()
                                        },
                                        onError = { err ->
                                            loading = false
                                            if (err.contains("active session", ignoreCase = true) || err.contains("409", ignoreCase = true)) {
                                                pendingSwitch = {
                                                    loading = true
                                                    viewModel.stopAndReset {
                                                        viewModel.startGuest(
                                                            onSuccess = { loading = false; onProfileSelected() },
                                                            onError = { loading = false; errorMsg = it },
                                                        )
                                                    }
                                                }
                                            } else {
                                                errorMsg = err
                                            }
                                        },
                                    )
                                },
                            )
                        }
                        is PickerItem.AddProfile -> {
                            AddProfileAvatar(
                                enabled = !loading,
                                onClick = {
                                    haptic(context, 25)
                                    showAddDialog = true
                                },
                            )
                        }
                    }
                }
            }

            // Loading indicator
            if (loading) {
                Spacer(Modifier.height(16.dp))
                CircularProgressIndicator(
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp,
                    color = colors.green,
                )
            }
        }
    }

    // Confirm end workout to switch profile
    if (pendingSwitch != null) {
        AlertDialog(
            onDismissRequest = { pendingSwitch = null },
            containerColor = colors.card,
            titleContentColor = colors.text,
            textContentColor = colors.text2,
            title = { Text("End current workout?", fontWeight = FontWeight.Bold) },
            text = { Text("Switching profiles will end your active workout.") },
            confirmButton = {
                TextButton(onClick = {
                    val action = pendingSwitch
                    pendingSwitch = null
                    action?.invoke()
                }) {
                    Text("End & Switch", color = colors.red)
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingSwitch = null }) {
                    Text("Cancel", color = colors.text3)
                }
            },
        )
    }

    // Add profile dialog
    if (showAddDialog) {
        AddProfileDialog(
            onDismiss = { showAddDialog = false },
            onCreateProfile = { name, color ->
                showAddDialog = false
                val initials = name.split(" ")
                    .filter { it.isNotBlank() }
                    .take(2)
                    .map { it.first().uppercaseChar() }
                    .joinToString("")
                    .ifEmpty { name.take(1).uppercase() }
                viewModel.createProfile(
                    name = name,
                    color = color,
                    initials = initials,
                    onSuccess = { profile ->
                        // Auto-select the new profile
                        viewModel.selectProfile(
                            id = profile.id,
                            onSuccess = onProfileSelected,
                        )
                    },
                    onError = { err -> errorMsg = err },
                )
            },
        )
    }
}

/** Sealed class for picker row items. */
private sealed class PickerItem(val key: String) {
    data class UserProfile(val profile: Profile) : PickerItem("profile-${profile.id}")
    data object Guest : PickerItem("guest")
    data object AddProfile : PickerItem("add-profile")
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
private fun ProfileAvatar(
    profile: Profile,
    color: Color,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val colors = LocalPrecorColors.current
    val serverPreferences: ServerPreferences = koinInject()
    val serverUrl by serverPreferences.serverUrl.collectAsState(initial = "")

    val avatarUrl = if (profile.hasAvatar && serverUrl.isNotBlank()) {
        "${serverUrl.trimEnd('/')}/api/profiles/${profile.id}/avatar"
    } else null

    val avatarBitmap = rememberAvatarBitmap(avatarUrl)

    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .width(88.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(80.dp)
                .clip(CircleShape)
                .background(color),
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
                    text = profile.initials,
                    fontSize = 26.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color(0xFF1E1D1B),
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            text = profile.name,
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = colors.text,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 88.dp),
        )
    }
}

@Composable
private fun GuestAvatar(
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val colors = LocalPrecorColors.current
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .width(88.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(80.dp)
                .background(
                    brush = androidx.compose.ui.graphics.Brush.linearGradient(
                        colors = listOf(Color(0xFFE8E4DF), Color(0xFFD4C4A8)),
                    ),
                    shape = androidx.compose.foundation.shape.CircleShape,
                ),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "\uD83D\uDC4B",
                fontSize = 32.sp,
            )
        }
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Guest",
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = colors.text,
            textAlign = TextAlign.Center,
        )
        Text(
            text = "Jump right in",
            fontSize = 11.sp,
            color = colors.text4,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun AddProfileAvatar(
    enabled: Boolean,
    onClick: () -> Unit,
) {
    val colors = LocalPrecorColors.current
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .width(88.dp)
            .clip(RoundedCornerShape(12.dp))
            .clickable(enabled = enabled, onClick = onClick)
            .padding(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(80.dp)
                .clip(CircleShape)
                .background(colors.fill2),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "+",
                fontSize = 28.sp,
                fontWeight = FontWeight.Light,
                color = colors.text3,
            )
        }
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Add Profile",
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = colors.text3,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun AddProfileDialog(
    onDismiss: () -> Unit,
    onCreateProfile: (name: String, color: String) -> Unit,
) {
    val colors = LocalPrecorColors.current
    var name by remember { mutableStateOf("") }
    var selectedColor by remember { mutableStateOf(AVATAR_COLORS[0]) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = colors.card,
        titleContentColor = colors.text,
        textContentColor = colors.text2,
        title = {
            Text(
                text = "New Profile",
                fontWeight = FontWeight.Bold,
            )
        },
        text = {
            Column(
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(24) },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Name") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(
                        onDone = {
                            if (name.isNotBlank()) onCreateProfile(name.trim(), selectedColor)
                        },
                    ),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = colors.green,
                        unfocusedBorderColor = colors.separator,
                        cursorColor = colors.green,
                        focusedLabelColor = colors.green,
                        unfocusedLabelColor = colors.text3,
                        focusedTextColor = colors.text,
                        unfocusedTextColor = colors.text,
                    ),
                )

                // Color picker
                Text(
                    text = "Color",
                    fontSize = 13.sp,
                    color = colors.text3,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    AVATAR_COLORS.forEach { hex ->
                        val avatarColor = parseHexColor(hex)
                        val isSelected = hex == selectedColor
                        Box(
                            modifier = Modifier
                                .size(40.dp)
                                .clip(CircleShape)
                                .background(avatarColor)
                                .then(
                                    if (isSelected) {
                                        Modifier.border(3.dp, colors.green, CircleShape)
                                    } else {
                                        Modifier
                                    }
                                )
                                .clickable { selectedColor = hex },
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (name.isNotBlank()) onCreateProfile(name.trim(), selectedColor)
                },
                enabled = name.isNotBlank(),
            ) {
                Text(
                    text = "Create",
                    color = if (name.isNotBlank()) colors.green else colors.text4,
                )
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel", color = colors.text3)
            }
        },
    )
}
