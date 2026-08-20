import { z } from 'zod'
import { env } from '@/lib/env'
import { mockId, providerFetch, simulateLatency } from './http'
import type { IntegrationProvider, ProviderResult, TokenSet } from './types'
import { ProviderError } from './types'

/** Notion adapter — satisfies TASK actions by creating a database page. */

const payloadSchema = z.object({
  title: z.string().min(1, 'A task needs a title'),
  dueAt: z.coerce.date().optional(),
  assignee: z.string().optional(),
  notes: z.string().optional(),
  /** Target database. Falls back to NOTION_TASK_DATABASE_ID when omitted. */
  projectId: z.string().optional(),
  priority: z.enum(['HIGH', 'MEDIUM', 'LOW']).optional(),
})

export type TaskPayload = z.infer<typeof payloadSchema>

interface NotionPage {
  id: string
  url?: string
}

const AUTH = 'https://api.notion.com/v1/oauth/authorize'
const TOKEN = 'https://api.notion.com/v1/oauth/token'
const API = 'https://api.notion.com/v1'
const VERSION = '2022-06-28'

export const notion: IntegrationProvider<TaskPayload, NotionPage> = {
  id: 'notion',
  displayName: 'Notion',
  capability: 'TASK',
  auth: 'oauth',
  scopes: [],
  // Notion has no idempotency key on page creation; a retry after an uncertain
  // failure could duplicate a page. Duplicated tasks are noisy but harmless, so
  // retry is still allowed — unlike Gmail, where a duplicate reaches a person.
  idempotent: false,

  isConfigured() {
    const e = env()
    return Boolean((e.NOTION_CLIENT_ID && e.NOTION_CLIENT_SECRET) || e.NOTION_API_KEY)
  },

  schema: payloadSchema,
  validate: (payload) => payloadSchema.parse(payload),

  preview(payload, ctx) {
    return {
      provider: 'notion',
      consequence: `Creates a Notion page in your task database${
        payload.assignee ? ` assigned to ${payload.assignee}` : ''
      }.`,
      fields: [
        { label: 'Title', value: payload.title },
        ...(payload.dueAt
          ? [{
              label: 'Due',
              value: new Intl.DateTimeFormat('en-GB', {
                timeZone: ctx.timeZone,
                day: 'numeric',
                month: 'short',
                year: 'numeric',
              }).format(payload.dueAt),
            }]
          : []),
        ...(payload.assignee ? [{ label: 'Assignee', value: payload.assignee }] : []),
        ...(payload.notes ? [{ label: 'Notes', value: truncate(payload.notes, 180) }] : []),
      ],
    }
  },

  authorizeUrl(state, redirectUri) {
    const params = new URLSearchParams({
      client_id: env().NOTION_CLIENT_ID ?? '',
      redirect_uri: redirectUri,
      response_type: 'code',
      owner: 'user',
      state,
    })
    // Notion's OAuth does not support PKCE; the signed single-use state token
    // remains the CSRF defence (SPEC-002 §4).
    return `${AUTH}?${params.toString()}`
  },

  async exchangeCode(code, redirectUri) {
    const basic = Buffer.from(
      `${env().NOTION_CLIENT_ID}:${env().NOTION_CLIENT_SECRET}`,
    ).toString('base64')
    const json = await providerFetch<{
      access_token: string
      workspace_id?: string
      workspace_name?: string
      bot_id?: string
    }>(TOKEN, {
      provider: 'notion',
      method: 'POST',
      headers: {
        authorization: `Basic ${basic}`,
        'content-type': 'application/json',
        'Notion-Version': VERSION,
      },
      body: JSON.stringify({ grant_type: 'authorization_code', code, redirect_uri: redirectUri }),
    })
    return {
      accessToken: json.access_token,
      externalAccountId: json.workspace_id,
      accountLabel: json.workspace_name,
    }
  },

  async refresh(): Promise<TokenSet> {
    // Notion access tokens do not expire, so there is nothing to refresh. A
    // revoked token surfaces as a 401 and becomes `needsReauth` (SPEC-002 §4).
    throw new ProviderError({
      code: 'refresh_unsupported',
      message: 'Notion tokens do not expire; reconnect the workspace instead.',
    })
  },

  async execute(payload, ctx) {
    const summary = `Created Notion task “${payload.title}”.`

    if (ctx.mode === 'mock') {
      await simulateLatency()
      const id = mockId('page', ctx.idempotencyKey)
      return {
        externalId: id,
        externalUrl: `https://notion.so/${id.replace(/_/g, '')}`,
        summary,
        raw: { id },
        simulated: true,
      }
    }

    const databaseId = payload.projectId ?? process.env.NOTION_TASK_DATABASE_ID
    if (!databaseId) {
      throw new ProviderError({
        code: 'missing_database',
        message:
          'No Notion database selected. Set a project id on the action, or configure ' +
          'NOTION_TASK_DATABASE_ID.',
      })
    }
    if (!ctx.accessToken) {
      throw new ProviderError({
        code: 'not_connected',
        message: 'Notion is not connected. Connect it in Settings → Integrations.',
      })
    }

    const page = await providerFetch<NotionPage>(`${API}/pages`, {
      provider: 'notion',
      method: 'POST',
      idempotent: false,
      headers: {
        authorization: `Bearer ${ctx.accessToken}`,
        'content-type': 'application/json',
        'Notion-Version': VERSION,
      },
      body: JSON.stringify({
        parent: { database_id: databaseId },
        properties: {
          Name: { title: [{ text: { content: payload.title } }] },
          ...(payload.dueAt
            ? { 'Due date': { date: { start: payload.dueAt.toISOString() } } }
            : {}),
          ...(payload.assignee
            ? { Assignee: { rich_text: [{ text: { content: payload.assignee } }] } }
            : {}),
          ...(payload.priority ? { Priority: { select: { name: payload.priority } } } : {}),
        },
        children: payload.notes
          ? [
              {
                object: 'block',
                type: 'paragraph',
                paragraph: { rich_text: [{ text: { content: truncate(payload.notes, 1900) } }] },
              },
            ]
          : [],
      }),
    })

    const result: ProviderResult<NotionPage> = {
      externalId: page.id,
      externalUrl: page.url,
      summary,
      raw: page,
      simulated: false,
    }
    return result
  },
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`
}
