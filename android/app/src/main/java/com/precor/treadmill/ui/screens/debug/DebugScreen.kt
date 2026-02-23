package com.precor.treadmill.ui.screens.debug

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.viewmodel.KVEntry
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.launch
import org.koin.compose.koinInject
import org.koin.androidx.compose.koinViewModel

private enum class DebugTab { STREAM, LOG }

@Composable
fun DebugScreen(
    viewModel: TreadmillViewModel = koinViewModel(),
    api: TreadmillApi = koinInject(),
) {
    val colors = LocalPrecorColors.current
    val status by viewModel.status.collectAsState()
    val kvLog by viewModel.kvLog.collectAsState()

    var tab by remember { mutableStateOf(DebugTab.STREAM) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bg)
            .statusBarsPadding(),
    ) {
        // Motor cache
        MotorCache(motor = status.motor)

        // Tab bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(44.dp),
        ) {
            DebugTab.entries.forEach { t ->
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .clickable { tab = t },
                    contentAlignment = Alignment.Center,
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Spacer(Modifier.weight(1f))
                        Text(
                            text = t.name,
                            color = if (tab == t) colors.text else colors.text4,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.SemiBold,
                            letterSpacing = 0.5.sp,
                        )
                        Spacer(Modifier.weight(1f))
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(2.dp)
                                .background(
                                    if (tab == t) colors.teal else colors.bg
                                ),
                        )
                    }
                }
            }
        }

        HorizontalDivider(color = colors.fill2, thickness = 1.dp)

        // Tab content
        when (tab) {
            DebugTab.STREAM -> KVStream(kvLog = kvLog)
            DebugTab.LOG -> LogViewer(api = api)
        }
    }
}

@Composable
private fun MotorCache(motor: Map<String, String>) {
    val colors = LocalPrecorColors.current
    val keys = motor.keys.sorted()

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp)
            .padding(top = 8.dp, bottom = 6.dp),
    ) {
        Text(
            text = "MOTOR LAST VALUES",
            color = colors.orange,
            fontSize = 10.sp,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(bottom = 4.dp),
        )

        if (keys.isEmpty()) {
            Text(
                text = "Waiting...",
                color = colors.text4,
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace,
            )
        } else {
            // Flow layout approximation with wrapping Row
            @OptIn(ExperimentalLayoutApi::class)
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                keys.forEach { key ->
                    Row {
                        Text(
                            text = "$key:",
                            color = colors.text4,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            maxLines = 1,
                        )
                        Text(
                            text = motor[key] ?: "",
                            color = colors.orange,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
    }

    HorizontalDivider(color = colors.fill2, thickness = 2.dp)
}

@Composable
private fun KVStream(kvLog: List<KVEntry>) {
    val colors = LocalPrecorColors.current
    val listState = rememberLazyListState()

    // Auto-scroll to bottom when near bottom
    LaunchedEffect(kvLog.size) {
        val info = listState.layoutInfo
        val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: 0
        val totalItems = info.totalItemsCount
        if (totalItems == 0 || totalItems - lastVisible < 5) {
            listState.animateScrollToItem(maxOf(0, kvLog.size - 1))
        }
    }

    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize(),
    ) {
        items(kvLog, key = { "${it.ts}-${it.src}-${it.key}-${it.value}" }) { entry ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 0.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Timestamp
                Text(
                    text = entry.ts,
                    color = colors.text4,
                    fontSize = 10.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    modifier = Modifier.width(68.dp),
                )
                // Direction arrow
                Text(
                    text = if (entry.src == "motor") "\u25C2" else "\u25B8",
                    color = if (entry.src == "motor") colors.orange else colors.teal,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    modifier = Modifier.width(12.dp),
                )
                // Key
                Text(
                    text = entry.key,
                    color = colors.text3,
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                    modifier = Modifier.width(42.dp),
                )
                // Value
                Text(
                    text = entry.value,
                    color = if (entry.src == "motor") colors.orange else colors.teal,
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines = 1,
                )
            }
        }
    }
}

@Composable
private fun LogViewer(api: TreadmillApi) {
    val colors = LocalPrecorColors.current
    val scope = rememberCoroutineScope()
    var lines by remember { mutableStateOf<List<String>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }

    fun fetchLog() {
        loading = true
        scope.launch {
            runCatching {
                val res = api.getLog(200)
                lines = res.lines
            }.onFailure {
                lines = listOf("(failed to fetch log)")
            }
            loading = false
        }
    }

    LaunchedEffect(Unit) { fetchLog() }

    Column(modifier = Modifier.fillMaxSize()) {
        // Toolbar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = { fetchLog() },
                enabled = !loading,
                colors = ButtonDefaults.buttonColors(
                    containerColor = colors.fill,
                    contentColor = colors.text2,
                ),
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
            ) {
                Text(
                    text = if (loading) "Loading..." else "Refresh",
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                )
            }
            Text(
                text = "${lines.size} lines",
                color = colors.text4,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace,
            )
        }

        HorizontalDivider(color = colors.fill2, thickness = 1.dp)

        // Log content
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 8.dp, vertical = 4.dp),
        ) {
            lines.forEach { line ->
                Text(
                    text = line,
                    color = colors.text2,
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    lineHeight = 16.sp,
                )
            }
        }
    }
}
