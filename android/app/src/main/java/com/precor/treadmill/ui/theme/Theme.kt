package com.precor.treadmill.ui.theme

import android.app.Activity
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalContext

private val DarkColorScheme = darkColorScheme(
    primary = PrecorColors.Green,
    onPrimary = PrecorColors.Bg,
    secondary = PrecorColors.Blue,
    onSecondary = PrecorColors.Text,
    tertiary = PrecorColors.Yellow,
    onTertiary = PrecorColors.Bg,
    error = PrecorColors.Red,
    onError = PrecorColors.Text,
    background = PrecorColors.Bg,
    onBackground = PrecorColors.Text,
    surface = PrecorColors.Card,
    onSurface = PrecorColors.Text,
    surfaceVariant = PrecorColors.Elevated,
    onSurfaceVariant = PrecorColors.Text2,
    outline = PrecorColors.Separator,
    outlineVariant = PrecorColors.Fill,
    surfaceContainerLowest = PrecorColors.Bg,
    surfaceContainerLow = PrecorColors.Card,
    surfaceContainer = PrecorColors.Elevated,
    surfaceContainerHigh = PrecorColors.Tertiary,
    surfaceContainerHighest = PrecorColors.Tertiary,
)

@Composable
fun PrecorTreadmillTheme(content: @Composable () -> Unit) {
    val context = LocalContext.current

    SideEffect {
        (context as? ComponentActivity)?.enableEdgeToEdge()
    }

    CompositionLocalProvider(LocalPrecorColors provides PrecorColorScheme()) {
        MaterialTheme(
            colorScheme = DarkColorScheme,
            typography = PrecorTypography,
            shapes = PrecorShapes,
            content = content,
        )
    }
}
