package com.precor.treadmill.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.fmtDur
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import org.koin.androidx.compose.koinViewModel

@Composable
fun MiniStatusCard(
    onClick: () -> Unit = {},
    viewModel: TreadmillViewModel = koinViewModel(),
    modifier: Modifier = Modifier,
) {
    val colors = LocalPrecorColors.current
    val session by viewModel.derivedSession.collectAsState()
    val program by viewModel.derivedProgram.collectAsState()

    if (!session.active && !program.running) return

    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp)
            .background(
                color = colors.card,
                shape = MaterialTheme.shapes.medium,
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(
                    text = if (program.running && program.currentIv != null) {
                        program.currentIv?.name ?: "Running"
                    } else "Running",
                    color = colors.text2,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = "%.1f mph \u00B7 ${session.pace} min/mi".format(session.speedMph),
                    color = colors.text3,
                    fontSize = 11.sp,
                )
            }
            Text(
                text = if (session.active) session.elapsedDisplay else fmtDur(program.totalElapsed.toInt()),
                color = colors.text,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}
