plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Signature : uniquement via l'environnement (secrets CI), jamais de clé dans le dépôt.
// Option LAN explicite : amont en HTTP vers un hôte du réseau local (mode autonome sans certificat).
// Désactivée par défaut ; active la config réseau permissive et signale `player.insecure_upstream`.
val lanHttp: Boolean = (findProperty("vds.lanHttp") as String?) == "true"
// Poste de recette : ne pose pas DISALLOW_CONFIG_DATE_TIME (jamais en production).
val kioskRelaxed: Boolean = (findProperty("vds.kioskRelaxed") as String?) == "true"
val keystorePath: String? = System.getenv("VDS_KEYSTORE_PATH")

android {
    namespace = "app.vds.player"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.vds.player"
        minSdk = 28
        targetSdk = 34
        versionCode = (System.getenv("VDS_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VDS_VERSION_NAME") ?: "0.2.0"
        buildConfigField("boolean", "ALLOW_LAN_HTTP", lanHttp.toString())
        buildConfigField("boolean", "KIOSK_RELAXED", kioskRelaxed.toString())
        manifestPlaceholders["networkSecurityConfig"] =
            if (lanHttp) "@xml/network_security_config_lan" else "@xml/network_security_config"
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("VDS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("VDS_KEY_ALIAS")
                keyPassword = System.getenv("VDS_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePath != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    packaging {
        resources.excludes += setOf(
            "META-INF/AL2.0", "META-INF/LGPL2.1", "META-INF/DEPENDENCIES",
            "META-INF/INDEX.LIST", "META-INF/io.netty.versions.properties",
        )
    }
}

dependencies {
    implementation(project(":relay"))
    implementation("androidx.webkit:webkit:1.9.0")
}
