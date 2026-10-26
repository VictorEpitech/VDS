package app.vds.relay

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PairingTicketVaultTest {
    private companion object {
        const val T = "AbCdEf0123456789_-AbCdEf0123"
        const val ROOT = "https://vds.client.fr"
        const val HOSTED = "https://allia-innovation.fr/espace/affichage"
    }

    private class MemStore : TicketStore {
        var v: SealedTicket? = null
        override fun read() = v
        override fun write(t: SealedTicket) { v = t }
        override fun clear() { v = null }
    }

    private object Rev : TicketSealer {
        override fun seal(plain: String) = "S:" + plain.reversed()
        override fun open(sealed: String) = sealed.removePrefix("S:").reversed()
    }

    private var now = 1_000_000L
    private val store = MemStore()
    private val vault = PairingTicketVault(store, Rev) { now }

    @Test
    fun ticketIsStoredSealedNeverClear() {
        assertTrue(vault.put(T, ROOT))
        val s = assertNotNull(store.read())
        assertFalse(s.sealed.contains(T))
        assertEquals(ROOT, s.serverUrl)
    }

    @Test
    fun claimedOnceThenGone() {
        vault.put(T, HOSTED)
        assertEquals(T, vault.claim(HOSTED))
        assertNull(vault.claim(HOSTED))
        assertFalse(vault.hasPending())
    }

    @Test
    fun otherServerUrlGetsNothingAndPurges() {
        vault.put(T, ROOT)
        assertNull(vault.claim(HOSTED))
        assertNull(vault.claim(ROOT))
        assertNull(vault.claim(null))
    }

    @Test
    fun expiresAfterThirtyMinutes() {
        vault.put(T, ROOT)
        now += PairingTicketVault.TTL_MS + 1
        assertNull(vault.claim(ROOT))
        vault.put(T, ROOT)
        now -= 10
        assertNull(vault.claim(ROOT))
    }

    @Test
    fun malformedTicketsRejectedAndPreviousPurged() {
        vault.put(T, ROOT)
        for (bad in listOf(null, "", "short", "a b c d e f g h i j k l", "x".repeat(65), "abcdefghijklmnop;rm -rf", "é".repeat(20))) {
            assertFalse(vault.put(bad, ROOT), bad)
            assertFalse(vault.hasPending())
        }
    }

    @Test
    fun claimReplyIsJson() {
        vault.put(T, ROOT)
        assertEquals("{\"ticket\":\"$T\"}", vault.claimReply(ROOT))
        assertEquals("{}", vault.claimReply(ROOT))
    }

    @Test
    fun routerClaimsOnlyForMainFrameAndTrustedPage() {
        vault.put(T, ROOT)
        val replies = mutableListOf<String>()
        var trusted = true
        val r = BridgeRouter(trusted = { trusted }, onPing = {}, onReload = {}, tickets = vault, serverUrl = { ROOT })
        r.onMessage(false, "ticket:a1:claim") { replies += it }
        trusted = false
        r.onMessage(true, "ticket:a1:claim") { replies += it }
        assertTrue(replies.isEmpty())
        assertTrue(vault.hasPending())
        trusted = true
        r.onMessage(true, "ticket:a1:bogus") { replies += it }
        r.onMessage(true, "ticket:a1:claim:extra") { replies += it }
        assertTrue(replies.isEmpty())
        r.onMessage(true, "ticket:a1:claim") { replies += it }
        r.onMessage(true, "ticket:a2:claim") { replies += it }
        assertEquals(listOf("ticket:a1:{\"ticket\":\"$T\"}", "ticket:a2:{}"), replies)
    }
}
