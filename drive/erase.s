; erase.s: wipe one track on a 1541/1571 (in 1541 mode) by writing sync-free bytes.
;
; Runs as an "execute" job ($E0) from buffer 2 ($0500): the disk controller steps the
; head to the track in the header table ($0A), selects the speed zone, spins the motor
; up, then jumps here (Inside Commodore DOS, c64-inside-commodore-dos p.102; job queue
; and buffers p.103; write mode p.142, p.202).
;
; Writes 8192 bytes of $55 (01010101: no sync, legal GCR flux), more than one turn at any
; speed zone (zone 3 holds about 7700 bytes a turn), so no sync mark, header or data block
; survives on the track. Returns through the ROM's job exit $F969 with the job's result in
; A ($01 OK, $08 write protected), which ends up in job slot $02. $F969 is the same in the
; 1541 ROM (901229-05) and the 1571 ROM (310654-05, 1541 mode), checked against VICE's ROMs.
;
; Build: cl65 -t none --start-addr 0x500 -o erase.bin erase.s
; The bytes are embedded in diskmasher.py (ERASE_CODE); rebuild and update both together.

        .setcpu "6502"

DRVB    = $1C00         ; VIA 2 port B: bit 4 = write protect sense (0 = protected)
DATA    = $1C01         ; VIA 2 port A: GCR byte to/from the head
DDRA    = $1C03         ; port A direction
PCR     = $1C0C         ; CB2 selects read ($E0) or write ($C0) mode
JOBEXIT = $F969         ; store A as the job result, back to the job loop

        lda DRVB
        and #$10
        bne ok
        lda #$08        ; write protected: report it, touch nothing
        jmp JOBEXIT
ok:     lda #$55
        sta DATA        ; latch the pattern before the port drives the head
        lda #$FF
        sta DDRA
        lda PCR
        and #$1F
        ora #$C0
        sta PCR         ; write mode
        ldx #$20        ; 32 pages = 8192 bytes
        ldy #$00
        lda #$55
loop:   bvc loop        ; byte ready (SO pin sets V)
        clv
        sta DATA
        iny
        bne loop
        dex
        bne loop
last:   bvc last        ; let the last byte go out
        clv
        lda PCR
        ora #$E0
        sta PCR         ; read mode
        lda #$00
        sta DDRA
        lda #$01        ; OK
        jmp JOBEXIT
