package com.precor.treadmill.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.ChatRequest
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.haptic
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatSheet(
    sheetState: SheetState,
    onDismiss: () -> Unit,
    onToast: (String) -> Unit,
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var chatMsg by remember { mutableStateOf("") }
    var thinking by remember { mutableStateOf(false) }
    val focusRequester = remember { FocusRequester() }

    LaunchedEffect(sheetState.isVisible) {
        if (sheetState.isVisible) {
            focusRequester.requestFocus()
        }
    }

    val sendChat: () -> Unit = {
        val msg = chatMsg.trim()
        if (msg.isNotBlank() && !thinking) {
            chatMsg = ""
            thinking = true
            scope.launch {
                runCatching {
                    val res = api.sendChat(ChatRequest(msg))
                    onToast(res.text.ifBlank { "No response" })
                }.onFailure {
                    onToast("Error connecting to AI")
                }
                thinking = false
                haptic(context, 15)
                onDismiss()
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = colors.card,
        contentColor = colors.text,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .padding(bottom = 32.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = chatMsg,
                onValueChange = { if (it.length <= 500) chatMsg = it },
                modifier = Modifier
                    .weight(1f)
                    .focusRequester(focusRequester),
                placeholder = {
                    Text(
                        "Ask your AI coach...",
                        color = colors.text3,
                        fontSize = 15.sp,
                    )
                },
                singleLine = true,
                enabled = !thinking,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { sendChat() }),
                shape = CircleShape,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = colors.separator,
                    unfocusedBorderColor = colors.separator,
                    focusedContainerColor = colors.elevated,
                    unfocusedContainerColor = colors.elevated,
                    focusedTextColor = colors.text,
                    unfocusedTextColor = colors.text,
                    cursorColor = colors.green,
                ),
            )

            // Send button
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(
                        if (chatMsg.isBlank() || thinking) colors.fill
                        else colors.purple
                    )
                    .clickable(enabled = chatMsg.isNotBlank() && !thinking) { sendChat() },
                contentAlignment = Alignment.Center,
            ) {
                if (thinking) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                        color = colors.purple,
                    )
                } else {
                    Text(
                        text = "\u2191",
                        color = if (chatMsg.isBlank()) colors.text3 else colors.text,
                        fontSize = 18.sp,
                    )
                }
            }
        }
    }
}
