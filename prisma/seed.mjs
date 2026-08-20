/**
 * Seed data — a realistic meeting whose action items exercise every path through
 * the dashboard: each readiness group, each action type, each risk tier, a
 * superseded action, a dependency, a guardrail violation, and an already-executed
 * item.
 *
 * Plain .mjs rather than TypeScript so seeding needs no extra toolchain in the
 * Docker image.
 */
import { randomBytes, scrypt as scryptCb } from 'node:crypto'
import { promisify } from 'node:util'
import { PrismaClient } from '@prisma/client'

const db = new PrismaClient()

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * A real password for the seeded demo account — same scrypt scheme as
 * `src/lib/password.ts::hashPassword` (`s1.<salt>.<hash>`), reimplemented here rather
 * than imported because this script deliberately stays plain `.mjs` (no TS toolchain
 * needed to seed). FastAPI's auth has no dev-identity bypass — a deliberate choice, see
 * `apps/api/app/api/dependencies.py` — so a real login is the only legitimate way for the
 * smoke suite (or anyone) to reach this account's fixture data through that backend.
 */
const scrypt = promisify(scryptCb)
const DEV_PASSWORD = 'vowcraft-dev-account-password'

async function hashDevPassword() {
  const salt = randomBytes(16)
  const hash = await scrypt(DEV_PASSWORD.normalize('NFKC'), salt, 64, {
    N: 32_768, r: 8, p: 1, maxmem: 64 * 1024 * 1024,
  })
  return `s1.${salt.toString('base64url')}.${hash.toString('base64url')}`
}

/** Next weekday at `hour`:00 IST, at least `minDays` from now. */
function nextWeekday(hour, minDays = 1) {
  // IST is UTC+5:30 with no DST, so a fixed offset is correct here.
  const IST_OFFSET = 5.5 * HOUR
  const target = new Date(Date.now() + minDays * DAY)
  for (let i = 0; i < 10; i++) {
    const ist = new Date(target.getTime() + IST_OFFSET)
    const weekday = ist.getUTCDay()
    if (weekday !== 0 && weekday !== 6) {
      const midnightIst = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate())
      return new Date(midnightIst + hour * HOUR - IST_OFFSET)
    }
    target.setTime(target.getTime() + DAY)
  }
  return target
}

/** Next Saturday at 11:00 IST — used to demonstrate SCHED_WEEKEND blocking. */
function nextSaturday() {
  const IST_OFFSET = 5.5 * HOUR
  const cursor = new Date(Date.now() + DAY)
  for (let i = 0; i < 14; i++) {
    const ist = new Date(cursor.getTime() + IST_OFFSET)
    if (ist.getUTCDay() === 6) {
      const midnightIst = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate())
      return new Date(midnightIst + 11 * HOUR - IST_OFFSET)
    }
    cursor.setTime(cursor.getTime() + DAY)
  }
  return cursor
}

async function main() {
  const email = process.env.DEV_USER_EMAIL ?? 'demo@vowcraft.test'

  // Idempotent: re-running the seed replaces the demo data rather than duplicating.
  const existing = await db.user.findUnique({ where: { email } })
  if (existing) {
    await db.transcript.deleteMany({ where: { userId: existing.id } })
    await db.calendarBusyBlock.deleteMany({ where: { userId: existing.id } })
    await db.teamMember.deleteMany({ where: { userId: existing.id } })
  }

  const passwordHash = await hashDevPassword()
  const user = await db.user.upsert({
    where: { email },
    create: { email, name: 'Demo Reviewer', passwordHash, passwordUpdatedAt: new Date() },
    update: { name: 'Demo Reviewer', passwordHash, passwordUpdatedAt: new Date() },
  })

  await db.userSettings.upsert({
    where: { userId: user.id },
    create: {
      userId: user.id,
      timeZone: 'Asia/Kolkata',
      workdayStart: '09:00',
      workdayEnd: '18:00',
      allowWeekends: false,
      maxMeetingMinutes: 120,
      minBufferMinutes: 15,
      orgDomains: ['acme.test'],
      autoExecuteLowRisk: false,
      budgetApprovalLimit: 1000,
      orgCurrency: 'USD',
    },
    update: { orgDomains: ['acme.test'] },
  })

  const team = [
    { name: 'Demo Reviewer', email, role: 'Product' },
    { name: 'Marcus', email: 'marcus@acme.test', role: 'Engineering' },
    { name: 'Priya', email: 'priya@acme.test', role: 'Finance' },
    { name: 'Jordan', email: 'jordan@acme.test', role: 'Design' },
    { name: 'Peter', email: 'peter@acme.test', role: 'Sales' },
  ]
  await db.teamMember.createMany({
    data: team.map((m) => ({ ...m, userId: user.id })),
  })

  // ── busy blocks: one collides with the "vendor sync" item below (SCHED_CONFLICT),
  //    one is a protected lunch block (SCHED_DND).
  const collidingStart = nextWeekday(14, 2)
  await db.calendarBusyBlock.createMany({
    data: [
      {
        userId: user.id,
        title: 'Sprint planning',
        startsAt: collidingStart,
        endsAt: new Date(collidingStart.getTime() + 60 * MINUTE),
        kind: 'BUSY',
      },
      {
        userId: user.id,
        title: 'Lunch',
        startsAt: new Date(nextWeekday(13, 1).getTime()),
        endsAt: new Date(nextWeekday(13, 1).getTime() + 45 * MINUTE),
        kind: 'LUNCH',
      },
    ],
  })

  // ══════════════════════════════════════════════ meeting 1: Q3 budget sync ══
  const recordedAt = new Date(Date.now() - 3 * HOUR)
  const transcript = await db.transcript.create({
    data: {
      userId: user.id,
      title: 'Q3 budget sync',
      sourceType: 'UPLOAD',
      language: 'en',
      durationMs: 22 * 60_000,
      recordedAt,
      status: 'READY',
      summary:
        'Reviewed Q3 spend, agreed to hold the vendor contract until legal signs off, and assigned ' +
        'follow-ups on the pricing deck and the revised budget.',
    },
  })

  const speakers = await Promise.all(
    [
      { label: 'Speaker 1', displayName: 'Marcus', email: 'marcus@acme.test' },
      { label: 'Speaker 2', displayName: 'Priya', email: 'priya@acme.test' },
      { label: 'Speaker 3', displayName: 'Jordan', email: 'jordan@acme.test' },
    ].map((s) => db.speaker.create({ data: { ...s, transcriptId: transcript.id } })),
  )

  // A few segments with word-level timings, so the transcript player and the
  // "show what was said" affordance have real data to render.
  const segments = [
    { speaker: 0, startMs: 61_000, text: "Let's lock the budget review for Friday morning." },
    { speaker: 1, startMs: 128_000, text: "I'll get the revised budget over to Priya by Friday." },
    { speaker: 2, startMs: 305_000, text: 'We are holding the vendor contract until legal signs off.' },
    { speaker: 0, startMs: 452_000, text: 'Set up a meeting with Peter about the renewal.' },
    { speaker: 0, startMs: 501_000, text: 'Actually, make that a meeting with Peter and Jordan.' },
    { speaker: 1, startMs: 754_000, text: 'Someone should email the vendor about the delay, I think.' },
  ]

  for (const segment of segments) {
    const words = segment.text.split(' ')
    const perWord = Math.floor(4_200 / words.length)
    await db.segment.create({
      data: {
        transcriptId: transcript.id,
        speakerId: speakers[segment.speaker].id,
        startMs: segment.startMs,
        endMs: segment.startMs + 4_200,
        text: segment.text,
        words: {
          create: words.map((word, i) => ({
            text: word,
            startMs: segment.startMs + i * perWord,
            endMs: segment.startMs + (i + 1) * perWord,
            confidence: 0.92 + (i % 5) * 0.015,
          })),
        },
      },
    })
  }

  await db.decision.createMany({
    data: [
      {
        transcriptId: transcript.id,
        statement: 'Hold the vendor contract until legal signs off.',
        decidedBy: 'Jordan',
        sourceTimestampMs: 305_000,
        sourceQuote: 'We are holding the vendor contract until legal signs off.',
      },
      {
        transcriptId: transcript.id,
        statement: 'Budget review happens Friday morning, not Monday.',
        decidedBy: 'Marcus',
        sourceTimestampMs: 61_000,
        sourceQuote: "Let's lock the budget review for Friday morning.",
      },
    ],
  })

  const fridayMorning = nextWeekday(10, 1)

  /** The nine items of meeting 1: 4 READY, 2 NEEDS_CLARIFICATION, 3 INFORMATIONAL. */
  const items = [
    // ── READY ×4
    {
      description: 'Schedule the Q3 budget review with Priya and Jordan',
      actionType: 'CALENDAR',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      deadline: fridayMorning,
      priority: 'HIGH',
      confidence: 'HIGH',
      sourceTimestampMs: 61_000,
      sourceQuote: "Let's lock the budget review for Friday morning.",
      reasoning: 'Marcus proposed a specific slot and both attendees agreed in the following turn.',
      payload: {
        title: 'Q3 budget review',
        startsAt: fridayMorning.toISOString(),
        durationMinutes: 45,
        attendees: ['priya@acme.test', 'jordan@acme.test'],
        location: 'Meet',
        description: 'Walk through Q3 spend against plan.',
        timeZone: 'Asia/Kolkata',
      },
    },
    {
      description: 'Update the pricing deck with the revised Q3 numbers',
      actionType: 'TASK',
      ownerName: 'Demo Reviewer',
      ownerEmail: email,
      deadline: new Date(Date.now() + 4 * DAY),
      priority: 'MEDIUM',
      confidence: 'HIGH',
      sourceTimestampMs: 128_000,
      sourceQuote: "I'll get the revised budget over to Priya by Friday.",
      reasoning: 'Speaker accepted the task and named a deadline.',
      payload: {
        title: 'Update pricing deck with revised Q3 numbers',
        dueAt: new Date(Date.now() + 4 * DAY).toISOString(),
        notes: 'Pull the revised figures from the budget review deck.',
      },
    },
    {
      description: 'Draft the meeting summary for the finance channel',
      actionType: 'EMAIL',
      ownerName: 'Demo Reviewer',
      ownerEmail: email,
      deadline: new Date(Date.now() + 20 * HOUR),
      priority: 'LOW',
      confidence: 'HIGH',
      sourceTimestampMs: 690_000,
      sourceQuote: 'Can someone write up what we agreed and send it round?',
      reasoning: 'An explicit request with no named owner; defaulted to the meeting organiser.',
      payload: {
        to: ['priya@acme.test', 'jordan@acme.test'],
        subject: 'Q3 budget sync — what we agreed',
        body:
          'Summary of the Q3 budget sync:\n\n' +
          '• Budget review moved to Friday morning\n' +
          '• Vendor contract on hold until legal signs off\n' +
          '• Pricing deck to be updated with revised numbers\n',
        sendMode: 'draft',
      },
    },
    {
      description: 'Remind me to check whether legal signed off on the vendor contract',
      actionType: 'REMINDER',
      ownerName: 'Demo Reviewer',
      ownerEmail: email,
      deadline: new Date(Date.now() + 3 * DAY),
      priority: 'MEDIUM',
      confidence: 'HIGH',
      sourceTimestampMs: 320_000,
      sourceQuote: 'We are holding the vendor contract until legal signs off.',
      reasoning: 'A blocking dependency worth a follow-up, inferred from the recorded decision.',
      payload: {
        message: 'Has legal signed off on the vendor contract?',
        remindAt: new Date(Date.now() + 3 * DAY).toISOString(),
        channel: 'self',
      },
    },

    // ── NEEDS_CLARIFICATION ×2
    {
      description: 'Set up the vendor renewal meeting',
      actionType: 'CALENDAR',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      deadline: new Date(Date.now() + 6 * DAY),
      priority: 'MEDIUM',
      confidence: 'MEDIUM',
      sourceTimestampMs: 452_000,
      sourceQuote: 'Set up a meeting with Peter about the renewal.',
      reasoning: 'A meeting was requested, but no time was agreed and the attendee list changed later.',
      // Deliberately missing startsAt and attendees → "needs: startsAt, attendees".
      payload: { title: 'Vendor renewal discussion', durationMinutes: 30 },
    },
    {
      description: 'Email the vendor to tell them the renewal is delayed',
      actionType: 'EMAIL',
      ownerName: 'Priya',
      ownerEmail: 'priya@acme.test',
      deadline: new Date(Date.now() + 2 * DAY),
      priority: 'HIGH',
      // LOW confidence *and* an external recipient with sendMode 'send' — this is
      // the item that demonstrates HIGH risk plus POL_EXTERNAL_EMAIL blocking.
      confidence: 'LOW',
      sourceTimestampMs: 754_000,
      sourceQuote: 'Someone should email the vendor about the delay, I think.',
      reasoning: 'Hedged phrasing with no named owner; the extractor flagged this as uncertain.',
      payload: {
        to: ['accounts@vendor-external.example'],
        subject: 'Renewal timeline update',
        body: 'We need to push the renewal decision by two weeks while legal completes its review.',
        sendMode: 'send',
      },
    },

    // ── INFORMATIONAL ×3
    {
      description: 'Note: the team considers the current roadmap sequencing sound',
      actionType: 'NONE',
      ownerName: 'Jordan',
      priority: 'LOW',
      confidence: 'MEDIUM',
      sourceTimestampMs: 880_000,
      sourceQuote: 'Honestly the roadmap order feels right to me.',
      reasoning: 'An opinion with no committed action.',
      payload: {},
    },
    {
      description: 'Note: hiring for the finance analyst role is paused until Q4',
      actionType: 'NONE',
      ownerName: 'Priya',
      priority: 'MEDIUM',
      confidence: 'HIGH',
      sourceTimestampMs: 1_010_000,
      sourceQuote: 'We are not opening the analyst role until Q4 at the earliest.',
      reasoning: 'Context worth recording; no task follows from it.',
      payload: {},
    },
    {
      description: 'Book the offsite venue for the finance team',
      actionType: 'TASK',
      ownerName: 'Jordan',
      ownerEmail: 'jordan@acme.test',
      deadline: new Date(Date.now() + 10 * DAY),
      priority: 'LOW',
      confidence: 'MEDIUM',
      status: 'REJECTED', // rejected ⇒ INFORMATIONAL
      sourceTimestampMs: 1_140_000,
      sourceQuote: 'Maybe we should look at venues for the offsite.',
      reasoning: 'Speculative; a reviewer rejected it.',
      payload: { title: 'Book offsite venue' },
    },
  ]

  const created = []
  for (const item of items) {
    created.push(
      await db.actionItem.create({ data: { ...item, transcriptId: transcript.id } }),
    )
  }

  // ═══════════════════════════════════════ meeting 2: edge cases and history ══
  const transcript2 = await db.transcript.create({
    data: {
      userId: user.id,
      title: 'Vendor renewal follow-up',
      sourceType: 'MEET',
      language: 'en',
      durationMs: 11 * 60_000,
      recordedAt: new Date(Date.now() - 26 * HOUR),
      status: 'READY',
    },
  })

  // Superseded pair: the earlier, narrower action points at the later one.
  const supersedingItem = await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Schedule the renewal call with Peter and Jordan',
      actionType: 'CALENDAR',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      deadline: nextWeekday(11, 3),
      priority: 'HIGH',
      confidence: 'HIGH',
      sourceTimestampMs: 501_000,
      sourceQuote: 'Actually, make that a meeting with Peter and Jordan.',
      reasoning: 'Corrects the earlier single-attendee version of this action.',
      payload: {
        title: 'Vendor renewal call',
        startsAt: nextWeekday(11, 3).toISOString(),
        durationMinutes: 30,
        attendees: ['peter@acme.test', 'jordan@acme.test'],
        timeZone: 'Asia/Kolkata',
      },
    },
  })

  await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Schedule the renewal call with Peter',
      actionType: 'CALENDAR',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      priority: 'HIGH',
      confidence: 'MEDIUM',
      sourceTimestampMs: 452_000,
      sourceQuote: 'Set up a meeting with Peter about the renewal.',
      reasoning: 'Superseded 49 seconds later when Jordan was added.',
      supersededById: supersedingItem.id,
      payload: {
        title: 'Vendor renewal call',
        startsAt: nextWeekday(11, 3).toISOString(),
        durationMinutes: 30,
        attendees: ['peter@acme.test'],
      },
    },
  })

  // Guardrail demo: a weekend slot that collides with an existing event.
  await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Run the vendor sync on Saturday morning',
      actionType: 'CALENDAR',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      priority: 'MEDIUM',
      confidence: 'MEDIUM',
      status: 'APPROVED', // approved, yet still unexecutable — guardrails win
      sourceTimestampMs: 120_000,
      sourceQuote: 'Could we just do it Saturday morning to get it out of the way?',
      reasoning: 'A specific time was proposed; the workspace disallows weekends.',
      payload: {
        title: 'Vendor sync',
        startsAt: nextSaturday().toISOString(),
        durationMinutes: 180, // also exceeds the 120-minute maximum
        attendees: ['peter@acme.test'],
        timeZone: 'Asia/Kolkata',
      },
    },
  })

  // Dependency chain: the second cannot run until the first is EXECUTED.
  const blocker = await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Get legal sign-off on the revised vendor terms',
      actionType: 'TASK',
      ownerName: 'Priya',
      ownerEmail: 'priya@acme.test',
      deadline: new Date(Date.now() + 5 * DAY),
      priority: 'HIGH',
      confidence: 'HIGH',
      sourceTimestampMs: 210_000,
      sourceQuote: 'Legal has to look at the revised terms first.',
      payload: { title: 'Legal sign-off on revised vendor terms', assignee: 'priya@acme.test' },
    },
  })

  await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Countersign the vendor renewal agreement',
      actionType: 'TASK',
      ownerName: 'Marcus',
      ownerEmail: 'marcus@acme.test',
      priority: 'HIGH',
      confidence: 'HIGH',
      status: 'APPROVED',
      dependsOnId: blocker.id,
      sourceTimestampMs: 260_000,
      sourceQuote: 'Once legal is happy, I will countersign.',
      payload: { title: 'Countersign vendor renewal', assignee: 'marcus@acme.test' },
    },
  })

  // An already-executed item, so the execution lane and executionResult
  // rendering are visible immediately after seeding.
  const executedAt = new Date(Date.now() - 40 * MINUTE)
  await db.actionItem.create({
    data: {
      transcriptId: transcript2.id,
      description: 'Create the renewal tracking task',
      actionType: 'TASK',
      ownerName: 'Demo Reviewer',
      ownerEmail: email,
      priority: 'MEDIUM',
      confidence: 'HIGH',
      status: 'EXECUTED',
      provider: 'notion',
      executedAt,
      executionAttempts: 1,
      sourceTimestampMs: 330_000,
      sourceQuote: 'Let us at least track the renewal somewhere.',
      payload: { title: 'Vendor renewal tracking', assignee: email },
      executionResult: {
        version: 1,
        outcome: 'SUCCESS',
        provider: 'notion',
        mode: 'mock',
        simulated: true,
        externalId: 'page_mock_5a1c93e0',
        externalUrl: 'https://notion.so/pagemock5a1c93e0',
        summary: 'Created Notion task “Vendor renewal tracking”.',
        startedAt: new Date(executedAt.getTime() - 240).toISOString(),
        finishedAt: executedAt.toISOString(),
        durationMs: 240,
        attempts: [{ n: 1, outcome: 'SUCCESS', durationMs: 240 }],
        warnings: [],
        payloadUsed: { title: 'Vendor renewal tracking', assignee: email },
        error: null,
      },
    },
  })

  const total = await db.actionItem.count({ where: { transcript: { userId: user.id } } })
  console.log(
    `Seeded ${total} action items across 2 transcripts for ${email}.\n` +
      `  “Q3 budget sync” → 4 ready · 2 needs clarification · 3 informational\n` +
      `  “Vendor renewal follow-up” → superseded pair, weekend guardrail, dependency chain, one executed`,
  )
}

main()
  .catch((err) => {
    console.error(err)
    process.exit(1)
  })
  .finally(() => db.$disconnect())
