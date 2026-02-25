# Kotlinx Serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.precor.treadmill.**$$serializer { *; }
-keepclassmembers class com.precor.treadmill.** {
    *** Companion;
}
-keepclasseswithmembers class com.precor.treadmill.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# OkHttp
-dontwarn okhttp3.**
-dontwarn okio.**

# Koin DI
-keep class org.koin.** { *; }
-dontwarn org.koin.**

# Retrofit
-keepattributes Signature, InnerClasses, EnclosingMethod
-keep,allowshrinking,allowoptimization class retrofit2.** { *; }
-dontwarn retrofit2.**

# App data models (used by serialization)
-keep class com.precor.treadmill.data.remote.models.** { *; }

# Voice layer models (used by JSON building)
-keep class com.precor.treadmill.voice.** { *; }
