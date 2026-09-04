package com.precor.treadmill.data.remote

import com.precor.treadmill.data.remote.models.*
import okhttp3.MultipartBody
import retrofit2.Call
import retrofit2.http.*

interface TreadmillApi {

    // --- Status & Control ---

    @GET("/api/status")
    suspend fun getStatus(): StatusMessage

    @POST("/api/speed")
    suspend fun setSpeed(@Body request: SpeedRequest): StatusMessage

    @POST("/api/incline")
    suspend fun setIncline(@Body request: InclineRequest): StatusMessage

    @POST("/api/emulate")
    suspend fun setEmulate(@Body request: EmulateRequest): StatusMessage

    @POST("/api/proxy")
    suspend fun setProxy(@Body request: ProxyRequest): StatusMessage

    @POST("/api/reset")
    suspend fun reset(): GenericOkResponse

    // --- Programs ---

    @GET("/api/program")
    suspend fun getProgram(): ProgramMessage

    @POST("/api/program/generate")
    suspend fun generateProgram(@Body request: GenerateRequest): GenerateResponse

    @POST("/api/program/start")
    suspend fun startProgram(): ProgramMessage

    @POST("/api/program/quick-start")
    suspend fun quickStart(@Body request: QuickStartRequest): GenericOkResponse

    @POST("/api/program/adjust-duration")
    suspend fun adjustDuration(@Body request: AdjustDurationRequest): ProgramMessage

    @POST("/api/program/stop")
    suspend fun stopProgram(): ProgramMessage

    @POST("/api/program/pause")
    suspend fun pauseProgram(): ProgramMessage

    @POST("/api/program/skip")
    suspend fun skipInterval(): ProgramMessage

    @POST("/api/program/prev")
    suspend fun prevInterval(): ProgramMessage

    @POST("/api/program/extend")
    suspend fun extendInterval(@Body request: ExtendRequest): ProgramMessage

    // --- History ---

    @GET("/api/programs/history")
    suspend fun getHistory(): List<HistoryEntry>

    @POST("/api/programs/history/{id}/load")
    suspend fun loadFromHistory(@Path("id") id: String): LoadHistoryResponse

    @POST("/api/programs/history/{id}/resume")
    suspend fun resumeFromHistory(@Path("id") id: String): ProgramMessage

    // --- Workouts ---

    @GET("/api/workouts")
    suspend fun getWorkouts(): List<SavedWorkout>

    @POST("/api/workouts")
    suspend fun saveWorkout(@Body request: SaveWorkoutRequest): SaveWorkoutResponse

    @PUT("/api/workouts/{id}")
    suspend fun renameWorkout(@Path("id") id: String, @Body request: RenameWorkoutRequest): GenericOkResponse

    @DELETE("/api/workouts/{id}")
    suspend fun deleteWorkout(@Path("id") id: String): GenericOkResponse

    @POST("/api/workouts/{id}/load")
    suspend fun loadWorkout(@Path("id") id: String): LoadHistoryResponse

    // --- GPX ---

    @Multipart
    @POST("/api/gpx/upload")
    suspend fun uploadGpx(@Part file: MultipartBody.Part): GpxUploadResponse

    // --- Chat ---

    @POST("/api/chat")
    suspend fun sendChat(@Body request: ChatRequest): ChatResponse

    @POST("/api/chat/voice")
    suspend fun sendVoiceChat(@Body request: VoiceChatRequest): ChatResponse

    @POST("/api/voice/extract-intent")
    suspend fun extractIntent(@Body request: ExtractIntentRequest): ExtractIntentResponse

    @POST("/api/tts")
    suspend fun requestTts(@Body request: TtsRequest): TtsResponse

    // --- Generic Tool Execution ---

    @POST("/api/tool")
    fun execToolCall(@Body request: ToolCallRequest): Call<ToolCallResponse>

    // --- Heart Rate Monitor ---

    @GET("/api/hrm")
    suspend fun getHrmStatus(): HrmStatusResponse

    @POST("/api/hrm/select")
    suspend fun selectHrmDevice(@Body request: HrmSelectRequest): GenericOkResponse

    @POST("/api/hrm/forget")
    suspend fun forgetHrmDevice(): GenericOkResponse

    @POST("/api/hrm/scan")
    suspend fun scanHrmDevices(): GenericOkResponse

    // --- User Profile ---

    @GET("/api/user")
    suspend fun getUser(): UserProfile

    @PUT("/api/user")
    suspend fun updateUser(@Body request: UpdateUserRequest): UserProfile

    // --- Profiles ---

    @GET("/api/profiles")
    suspend fun getProfiles(): List<Profile>

    @POST("/api/profiles")
    suspend fun createProfile(@Body request: CreateProfileRequest): Profile

    @POST("/api/profile/select")
    suspend fun selectProfile(@Body request: SelectProfileRequest): GenericOkResponse

    @GET("/api/profile/active")
    suspend fun getActiveProfile(): ActiveProfileResponse

    @POST("/api/profile/guest")
    suspend fun startGuest(): GenericOkResponse

    @PUT("/api/profiles/{id}")
    suspend fun updateProfile(@Path("id") id: String, @Body request: UpdateProfileRequest): Profile

    @DELETE("/api/profiles/{id}")
    suspend fun deleteProfile(@Path("id") id: String): GenericOkResponse

    @POST("/api/profile/guest/convert")
    suspend fun convertGuest(@Body request: ConvertGuestRequest): Profile

    @Multipart
    @POST("/api/profiles/{id}/avatar")
    suspend fun uploadAvatar(@Path("id") id: String, @Part file: MultipartBody.Part): GenericOkResponse

    @DELETE("/api/profiles/{id}/avatar")
    suspend fun deleteAvatar(@Path("id") id: String): GenericOkResponse

    // --- Log & Config ---

    @GET("/api/log")
    suspend fun getLog(@Query("lines") lines: Int = 200): LogResponse

    @GET("/api/config")
    suspend fun getConfig(): AppConfig

    @GET("/api/voice/prompt/{id}")
    suspend fun getVoicePrompt(@Path("id") id: String): VoicePromptResponse
}
