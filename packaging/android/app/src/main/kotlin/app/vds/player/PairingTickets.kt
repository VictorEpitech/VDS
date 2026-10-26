package app.vds.player

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import app.vds.relay.PairingTicketVault
import app.vds.relay.SealedTicket
import app.vds.relay.TicketSealer
import app.vds.relay.TicketStore
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Ticket d'appairage au repos : SharedPreferences privées, contenu chiffré par une clé Android Keystore. */
object PairingTickets {
    fun vault(context: () -> Context): PairingTicketVault = PairingTicketVault(PrefsTicketStore(context), KeystoreSealer())
}

/** Le contexte est résolu à la demande : le coffre peut être construit dans un initialiseur d'Activity. */
private class PrefsTicketStore(private val context: () -> Context) : TicketStore {
    private val sp by lazy { context().applicationContext.getSharedPreferences("vds_ticket", Context.MODE_PRIVATE) }

    override fun read(): SealedTicket? {
        val s = sp.getString("sealed", null) ?: return null
        val u = sp.getString("url", null) ?: return null
        return SealedTicket(s, u, sp.getLong("at", 0))
    }

    override fun write(t: SealedTicket) {
        sp.edit().putString("sealed", t.sealed).putString("url", t.serverUrl).putLong("at", t.atMs).commit()
    }

    override fun clear() {
        sp.edit().clear().commit()
    }
}

private class KeystoreSealer : TicketSealer {
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return gen.generateKey()
    }

    override fun seal(plain: String): String {
        val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = c.iv + c.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    override fun open(sealed: String): String? {
        val raw = Base64.decode(sealed, Base64.NO_WRAP)
        if (raw.size <= IV_LEN) return null
        val c = Cipher.getInstance(TRANSFORM).apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, IV_LEN)) }
        return String(c.doFinal(raw, IV_LEN, raw.size - IV_LEN), Charsets.UTF_8)
    }

    private companion object {
        const val ALIAS = "vds_pairing_ticket"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV_LEN = 12
    }
}
