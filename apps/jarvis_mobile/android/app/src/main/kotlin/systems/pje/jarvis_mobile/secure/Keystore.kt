package systems.pje.jarvis_mobile.secure

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.KeyProtection
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

private const val ANDROID_KEYSTORE = "AndroidKeyStore"
private const val DEVICE_KEY = "jarvis_device_ec"
private const val PUSH_KEY = "jarvis_push_aes"
private const val STORE_KEY = "jarvis_store_aes"

private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

private fun b64(data: ByteArray): String = Base64.encodeToString(data, Base64.NO_WRAP)

/**
 * The phone's identity: an EC P-256 key generated inside the Android Keystore (StrongBox when available).
 * The private key never leaves secure hardware; the core only knows the public key from pairing and checks
 * signatures on token refresh and approvals.
 */
object DeviceKey {
    fun exists(): Boolean = keyStore().containsAlias(DEVICE_KEY)

    fun ensure(context: Context) {
        if (exists()) return
        fun spec(strongBox: Boolean) = KeyGenParameterSpec.Builder(DEVICE_KEY, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .apply { if (strongBox) setIsStrongBoxBacked(true) }
            .build()
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, ANDROID_KEYSTORE)
        val strongBox = context.packageManager.hasSystemFeature("android.hardware.strongbox_keystore")
        try {
            gen.initialize(spec(strongBox)); gen.generateKeyPair()
        } catch (e: Exception) {
            if (!strongBox) throw e
            gen.initialize(spec(false)); gen.generateKeyPair()
        }
    }

    fun publicKeySpki(): String = b64(keyStore().getCertificate(DEVICE_KEY).publicKey.encoded)

    /** DER-encoded ECDSA signature (SHA256withECDSA), base64 – verified by jarvis.mobile.tokens. */
    fun sign(message: ByteArray): String {
        val entry = keyStore().getEntry(DEVICE_KEY, null) as KeyStore.PrivateKeyEntry
        return Signature.getInstance("SHA256withECDSA").run {
            initSign(entry.privateKey); update(message); b64(sign())
        }
    }

    fun delete() {
        runCatching { keyStore().deleteEntry(DEVICE_KEY) }
    }
}

/** AES-256 key for end-to-end encrypted push payloads, imported once into the Keystore (non-exportable). */
object PushKey {
    fun import(raw: ByteArray) {
        val ks = keyStore()
        ks.setEntry(
            PUSH_KEY,
            KeyStore.SecretKeyEntry(SecretKeySpec(raw, "AES")),
            KeyProtection.Builder(KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        raw.fill(0)
    }

    fun decrypt(nonce: ByteArray, ciphertext: ByteArray, aad: ByteArray): ByteArray {
        val key = keyStore().getKey(PUSH_KEY, null) as SecretKey
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, nonce))
            updateAAD(aad)
            doFinal(ciphertext)
        }
    }

    fun exists(): Boolean = keyStore().containsAlias(PUSH_KEY)
    fun delete() {
        runCatching { keyStore().deleteEntry(PUSH_KEY) }
    }
}

/**
 * Small encrypted key/value store (refresh token, device id, gateway, pin). Values are AES-GCM encrypted
 * with a Keystore key; SharedPreferences only ever holds ciphertext.
 */
class SecureStore(context: Context, name: String = "jarvis_secure") {
    private val prefs = context.getSharedPreferences(name, Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val ks = keyStore()
        (ks.getKey(STORE_KEY, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        gen.init(
            KeyGenParameterSpec.Builder(STORE_KEY, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return gen.generateKey()
    }

    @Synchronized
    fun put(name: String, value: String?) {
        if (value == null) {
            prefs.edit().remove(name).apply(); return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val ct = cipher.doFinal(value.toByteArray())
        prefs.edit().putString(name, b64(cipher.iv) + ":" + b64(ct)).commit()
    }

    @Synchronized
    fun get(name: String): String? {
        val raw = prefs.getString(name, null) ?: return null
        val (iv, ct) = raw.split(":").let { it[0] to it[1] }
        return runCatching {
            Cipher.getInstance("AES/GCM/NoPadding").run {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
                String(doFinal(Base64.decode(ct, Base64.NO_WRAP)))
            }
        }.getOrNull()
    }

    fun clear() {
        prefs.edit().clear().commit()
    }
}
