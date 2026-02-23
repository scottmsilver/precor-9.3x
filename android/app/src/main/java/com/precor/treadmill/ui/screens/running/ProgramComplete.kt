package com.precor.treadmill.ui.screens.running

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel

@Composable
fun ProgramComplete(
    viewModel: TreadmillViewModel,
    onVoice: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val sess by viewModel.derivedSession.collectAsState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "Workout Complete",
            color = Color(0xFF6BC89B),
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(16.dp))

        // Summary stats
        Row(
            horizontalArrangement = Arrangement.spacedBy(24.dp),
            verticalAlignment = Alignment.Top,
        ) {
            StatItem("Time", sess.elapsedDisplay)
            StatItem("Distance", "${sess.distDisplay} mi")
            StatItem("Vert", "${sess.vertDisplay} ft")
        }

        Spacer(Modifier.height(24.dp))

        // Voice prompt button
        Button(
            onClick = onVoice,
            colors = ButtonDefaults.buttonColors(
                containerColor = Color(0xFF8B7FA0).copy(alpha = 0.15f),
                contentColor = Color(0xFF8B7FA0),
            ),
            shape = RoundedCornerShape(9999.dp),
        ) {
            Text("Start a new workout by voice", fontSize = 14.sp)
        }
    }
}

@Composable
private fun StatItem(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = value,
            color = Color(0xFFE8E4DF),
            fontSize = 18.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = label,
            color = Color(0x59E8E4DF),
            fontSize = 11.sp,
        )
    }
}
