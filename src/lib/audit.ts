import type { Prisma, PrismaClient } from '@prisma/client'
import { logger } from './logger'

/**
 * Append-only audit trail (SPEC-003 §7). This module exposes `record()` and
 * nothing else — there is deliberately no update or delete helper anywhere in
 * the codebase, and production grants the app role INSERT/SELECT only.
 *
 * `tx` is required so a caller can put the audit write in the same transaction
 * as the state change it describes. An EXECUTED item without its audit row is a
 * compliance defect, so the two commit together or not at all (SPEC-002 §5 §10-11).
 */

export type AuditEvent =
  | 'action_item.created'
  | 'action_item.edited'
  | 'action_item.approved'
  | 'action_item.rejected'
  | 'action_item.deferred'
  | 'action_item.reopened'
  | 'action_item.execution_requested'
  | 'action_item.executed'
  | 'action_item.execution_failed'
  | 'action_item.guardrail_blocked'
  | 'integration.connected'
  | 'integration.reauth_required'
  | 'credential.saved'
  | 'credential.deleted'
  | 'credential.verified'
  | 'profile.updated'
  | 'profile.password_changed'
  | 'profile.onboarding'
  | 'profile.deletion_requested'
  | 'profile.deletion_cancelled'
  | 'profile.deleted'
  | 'settings.updated'
  | 'auth.signed_up'
  | 'auth.signed_in'
  | 'auth.sign_in_failed'
  | 'auth.signed_out'
  | 'auth.password_reset_requested'
  | 'auth.password_reset_completed'
  | 'auth.identity_linked'
  | 'auth.identity_created'
  | 'transcript.uploaded'
  | 'transcript.extracted'
  | 'transcript.extraction_failed'
  | 'transcript.deleted'
  | 'brd.created'
  | 'brd.revised'
  | 'brd.renamed'
  | 'brd.deleted'
  | 'brd.generation_failed'

export interface AuditInput {
  event: AuditEvent
  actorId?: string | null
  actorType?: 'USER' | 'SYSTEM' | 'AGENT'
  actionItemId?: string | null
  /**
   * Snapshots and context. Typed as `unknown` rather than Prisma's
   * `InputJsonValue`: callers hand over plain diff objects assembled from row
   * fields, and forcing them to satisfy Prisma's recursive JSON type at every
   * call site adds casts without adding safety. Serialisation happens once, below.
   */
  before?: unknown
  after?: unknown
  metadata?: unknown
  requestId?: string
}

/** Drops `undefined` and anything non-serialisable before it reaches Prisma. */
function toJson(value: unknown): Prisma.InputJsonValue | undefined {
  if (value === undefined || value === null) return undefined
  return JSON.parse(JSON.stringify(value)) as Prisma.InputJsonValue
}

type Client = PrismaClient | Prisma.TransactionClient

export async function record(client: Client, input: AuditInput): Promise<void> {
  await client.auditLog.create({
    data: {
      event: input.event,
      actorType: input.actorType ?? 'USER',
      actorId: input.actorId ?? null,
      actionItemId: input.actionItemId ?? null,
      before: toJson(input.before),
      after: toJson(input.after),
      metadata: toJson(input.metadata),
      requestId: input.requestId ?? null,
    },
  })

  // The log line is the real-time twin of the durable row (SPEC-000 §6).
  logger.info('audit', {
    event: input.event,
    actionItemId: input.actionItemId,
    actorId: input.actorId,
    requestId: input.requestId,
  })
}
