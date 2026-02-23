package com.precor.treadmill.ui.components

import androidx.compose.animation.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay

@Composable
fun ToastBanner(
    message: String,
    visible: Boolean,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        enter = slideInVertically(initialOffsetY = { -it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { -it }) + fadeOut(),
        modifier = modifier.fillMaxWidth(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp)
                .background(
                    color = Color(0xFF2A2925),
                    shape = RoundedCornerShape(12.dp),
                )
                .padding(horizontal = 20.dp, vertical = 14.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = message,
                color = Color(0xFFE8E4DF),
                fontSize = 14.sp,
                textAlign = TextAlign.Center,
            )
        }
    }
}

/**
 * Stateful toast that auto-dismisses. Collect from a SharedFlow<String>.
 */
@Composable
fun ToastHost(
    toastFlow: kotlinx.coroutines.flow.SharedFlow<String>,
    modifier: Modifier = Modifier,
) {
    var message by remember { mutableStateOf("") }
    var visible by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        toastFlow.collect { msg ->
            message = msg
            visible = true
            delay(8000)
            visible = false
        }
    }

    ToastBanner(
        message = message,
        visible = visible,
        modifier = modifier,
    )
}
