package com.precor.treadmill.ui.theme

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// Ported from ui/src/styles/tokens.css
object PrecorColors {
    // Backgrounds
    val Bg = Color(0xFF121210)
    val Card = Color(0xFF1E1D1B)
    val Elevated = Color(0xFF2A2925)
    val Tertiary = Color(0xFF36342F)
    val Input = Color(0xFF2A2925)

    // Text
    val Text = Color(0xFFE8E4DF)
    val Text2 = Color(0x99E8E4DF)
    val Text3 = Color(0x59E8E4DF)
    val Text4 = Color(0x2EE8E4DF)

    // Accents
    val Green = Color(0xFF6BC89B)
    val Red = Color(0xFFC45C52)
    val Blue = Color(0xFF6B8FA0)
    val Yellow = Color(0xFFB8A87A)
    val Orange = Color(0xFFA69882)
    val Pink = Color(0xFFB06B72)
    val Purple = Color(0xFF8B7FA0)
    val Teal = Color(0xFF6B8F8B)

    // Fills & separators
    val Fill = Color(0x3D787880)
    val Fill2 = Color(0x29787880)
    val Separator = Color(0xA6545458)
}

@Immutable
data class PrecorColorScheme(
    val bg: Color = PrecorColors.Bg,
    val card: Color = PrecorColors.Card,
    val elevated: Color = PrecorColors.Elevated,
    val tertiary: Color = PrecorColors.Tertiary,
    val input: Color = PrecorColors.Input,
    val text: Color = PrecorColors.Text,
    val text2: Color = PrecorColors.Text2,
    val text3: Color = PrecorColors.Text3,
    val text4: Color = PrecorColors.Text4,
    val green: Color = PrecorColors.Green,
    val red: Color = PrecorColors.Red,
    val blue: Color = PrecorColors.Blue,
    val yellow: Color = PrecorColors.Yellow,
    val orange: Color = PrecorColors.Orange,
    val pink: Color = PrecorColors.Pink,
    val purple: Color = PrecorColors.Purple,
    val teal: Color = PrecorColors.Teal,
    val fill: Color = PrecorColors.Fill,
    val fill2: Color = PrecorColors.Fill2,
    val separator: Color = PrecorColors.Separator,
)

val LocalPrecorColors = staticCompositionLocalOf { PrecorColorScheme() }
