import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

fun loadProperties(path: String) = Properties().apply {
    val file = rootProject.file(path)
    if (file.isFile) file.inputStream().use(::load)
}

val configDir = rootProject.file(findProperty("kis.configDir") ?: ".")
val localProperties = loadProperties(File(configDir, "local.properties").path)
val signingProperties = loadProperties(File(configDir, "signing/signing.properties").path)

/** -Pname=value 가 있으면 그것을, 없으면 local.properties 의 값을 씁니다. */
fun setting(name: String): String? =
    (findProperty(name) as String?)?.takeIf { it.isNotBlank() } ?: localProperties.getProperty(name)

// 데스크톱 앱 프로젝트. 차트 서버(server/)와 화면(web/)은 여기서 그대로 가져옵니다.
val desktopDir: File = rootProject.file(setting("kis.desktopDir") ?: "..")
val desktopPython = layout.buildDirectory.dir("generated/desktop/python")

val syncDesktop = tasks.register<Sync>("syncDesktop") {
    group = "build"
    description = "데스크톱 앱의 server/, web/, desktop_login.py 를 가져옵니다."
    val source = desktopDir
    val required = listOf(
        "server/main.py", "server/config.py", "server/vault.py", "server/guard.py",
        "web/index.html", "desktop_login.py",
    )
    doFirst {
        val missing = required.filterNot { File(source, it).isFile }
        if (missing.isNotEmpty()) {
            throw GradleException(
                "데스크톱 프로젝트를 찾지 못했습니다: $source (없는 파일: $missing)\n" +
                    "local.properties 의 kis.desktopDir 을 확인하세요."
            )
        }
    }
    from(source) {
        include("server/**", "web/**", "desktop_login.py")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(desktopPython)
}

android {
    namespace = "com.kistradingview.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.kistradingview.app"
        minSdk = 26
        targetSdk = 36
        // build-android.ps1 이 빌드할 때마다 더 큰 값을 넘겨서, 새 APK 를 기존 앱 위에 바로 설치할 수 있습니다.
        versionCode = (setting("kis.versionCode") ?: "1").toInt()
        versionName = setting("kis.versionName") ?: "dev"
        buildConfigField("String", "DESKTOP_REVISION", "\"${setting("kis.desktopRevision") ?: "unknown"}\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk {
            // Python 3.13 은 64비트만 지원합니다. x86_64 는 에뮬레이터용입니다.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            val store = signingProperties.getProperty("storeFile")
            if (store != null) {
                storeFile = File(configDir, store)
                storePassword = signingProperties.getProperty("storePassword")
                keyAlias = signingProperties.getProperty("keyAlias")
                keyPassword = signingProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            // 디버그 앱은 별도 앱으로 설치되어 정식 앱과 그 데이터(저장한 키, 그린 선)를 건드리지 않습니다.
            applicationIdSuffix = ".debug"
        }
        release {
            isMinifyEnabled = false
            // 서명 키가 아직 없으면(build-android.ps1 이 처음 실행 때 만듭니다) 디버그 키로 서명합니다.
            signingConfig = signingConfigs.getByName(
                if (signingProperties.getProperty("storeFile") != null) "release" else "debug"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        // 앱에 넣는 Python 과 같은 버전(3.13)의 Python 이 빌드 PC 에 있어야 합니다.
        setting("kis.buildPython")?.let { buildPython(it) }
        pip {
            install("-r", rootProject.file("requirements-android.txt").path)
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(desktopPython)
        }
    }
}

// Chaquopy 가 Python 소스를 모으기 전에 데스크톱 코드를 가져옵니다.
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach {
    dependsOn(syncDesktop)
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-ktx:1.12.4")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.17.1")
    implementation("com.google.android.material:material:1.13.0")

    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
