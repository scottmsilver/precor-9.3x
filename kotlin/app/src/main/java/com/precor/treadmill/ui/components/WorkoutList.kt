package com.precor.treadmill.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.widget.Toast
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.SavedWorkout
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.util.fmtDur
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

@Composable
fun WorkoutList(
    variant: String,
    onAfterLoad: () -> Unit = {},
    onWorkoutDeleted: () -> Unit = {},
    refreshKey: Int = 0,
    modifier: Modifier = Modifier,
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var workouts by remember { mutableStateOf<List<SavedWorkout>>(emptyList()) }

    LaunchedEffect(refreshKey) {
        runCatching { workouts = api.getWorkouts() }.onFailure {
            Toast.makeText(context, "Failed to load workouts", Toast.LENGTH_SHORT).show()
        }
    }

    val handleLoad: (String) -> Unit = { id ->
        scope.launch {
            runCatching {
                val res = api.loadWorkout(id)
                if (res.ok) onAfterLoad()
            }.onFailure {
                Toast.makeText(context, "Failed to load workout", Toast.LENGTH_SHORT).show()
            }
        }
    }

    val handleDelete: (String) -> Unit = { id ->
        scope.launch {
            runCatching {
                val res = api.deleteWorkout(id)
                if (res.ok) {
                    workouts = workouts.filter { it.id != id }
                    onWorkoutDeleted()
                }
            }.onFailure {
                Toast.makeText(context, "Failed to delete workout", Toast.LENGTH_SHORT).show()
            }
        }
    }

    if (workouts.isEmpty()) {
        LegibleText(
            text = "No saved workouts yet",
            color = colors.text3,
            style = TextStyle(fontSize = 13.sp),
            modifier = modifier.padding(horizontal = 16.dp, vertical = 8.dp),
        )
        return
    }

    Column(
        modifier = modifier.padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        workouts.forEach { workout ->
            key(workout.id) {
                WorkoutCard(
                    workout = workout,
                    onLoad = { handleLoad(workout.id) },
                    onDelete = { handleDelete(workout.id) },
                )
            }
        }
    }
}

@Composable
private fun WorkoutCard(
    workout: SavedWorkout,
    onLoad: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LocalPrecorColors.current
    val name = workout.name.ifBlank { "Workout" }
    val intervals = workout.program?.intervals?.size ?: 0
    val totalSecs = workout.program?.intervals?.sumOf { it.duration }?.toInt() ?: 0
    val duration = fmtDur(totalSecs)

    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(
                color = colors.card,
                shape = MaterialTheme.shapes.medium,
            )
            .clickable { onLoad() }
            .padding(12.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = name,
                color = colors.text,
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = "$duration \u00B7 $intervals interval${if (intervals != 1) "s" else ""}",
                color = colors.text3,
                fontSize = 12.sp,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text = workout.usageText.ifBlank { "Never used" },
                color = colors.text3,
                fontSize = 11.sp,
            )
        }
        IconButton(
            onClick = onDelete,
            modifier = Modifier.size(32.dp),
        ) {
            Icon(
                imageVector = Icons.Outlined.Close,
                contentDescription = "Delete workout",
                tint = colors.text3,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}
