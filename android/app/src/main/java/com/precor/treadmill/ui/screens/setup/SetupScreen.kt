package com.precor.treadmill.ui.screens.setup

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.ui.theme.LocalPrecorColors
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

@Composable
fun SetupScreen(
    onConnected: () -> Unit,
    serverPreferences: ServerPreferences = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val scope = rememberCoroutineScope()

    var url by remember { mutableStateOf("https://192.168.1.14:8000") }
    var error by remember { mutableStateOf<String?>(null) }
    var connecting by remember { mutableStateOf(false) }

    fun connect() {
        val trimmed = url.trim()
        if (trimmed.isBlank()) {
            error = "Please enter a server URL"
            return
        }
        if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
            error = "URL must start with http:// or https://"
            return
        }
        error = null
        connecting = true
        scope.launch {
            serverPreferences.setServerUrl(trimmed.trimEnd('/'))
            onConnected()
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg)
            .systemBarsPadding(),
        contentAlignment = Alignment.Center,
    ) {
        Card(
            modifier = Modifier
                .widthIn(max = 400.dp)
                .padding(24.dp),
            colors = CardDefaults.cardColors(
                containerColor = colors.card,
            ),
            shape = MaterialTheme.shapes.large,
        ) {
            Column(
                modifier = Modifier.padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                Text(
                    text = "Precor Treadmill",
                    style = MaterialTheme.typography.headlineMedium,
                    color = colors.text,
                )

                Text(
                    text = "Enter the server address to connect.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = colors.text2,
                )

                OutlinedTextField(
                    value = url,
                    onValueChange = {
                        url = it
                        error = null
                    },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Server URL") },
                    placeholder = { Text("https://rpi:8000") },
                    singleLine = true,
                    isError = error != null,
                    supportingText = error?.let { msg ->
                        { Text(msg) }
                    },
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Go,
                    ),
                    keyboardActions = KeyboardActions(
                        onGo = { connect() },
                    ),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = colors.green,
                        unfocusedBorderColor = colors.separator,
                        cursorColor = colors.green,
                        focusedLabelColor = colors.green,
                        unfocusedLabelColor = colors.text3,
                        focusedTextColor = colors.text,
                        unfocusedTextColor = colors.text,
                        errorBorderColor = colors.red,
                        focusedPlaceholderColor = colors.text4,
                        unfocusedPlaceholderColor = colors.text4,
                    ),
                )

                Button(
                    onClick = { connect() },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !connecting,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = colors.green,
                        contentColor = colors.bg,
                        disabledContainerColor = colors.fill,
                        disabledContentColor = colors.text3,
                    ),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    if (connecting) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp,
                            color = colors.bg,
                        )
                    } else {
                        Text(
                            text = "Connect",
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }
    }
}
