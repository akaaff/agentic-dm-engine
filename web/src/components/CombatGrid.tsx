import { useState } from 'react'
import type { LiveBattleMap, LiveCharacter, TerrainType } from '../ws/sessionClient'
import { portraitUrl } from '../utils/portraits'

const CELL_SIZE = 40
const TOKEN_RADIUS = CELL_SIZE / 2 - 4

const TERRAIN_FILL: Record<TerrainType, string> = {
  floor: '#1c1924',
  wall: '#0a090d',
  difficult: '#3a2f1a',
  hazard: '#3a1a1a',
}

/** Dead/downed override the character's own distinct actorColors entry -
 * those two states matter more here than "whose token is this" once a
 * character can no longer act. */
function tokenColor(character: LiveCharacter, baseColor: string): string {
  if (character.is_dead) return '#4a4552'
  if (character.hp <= 0) return '#8a7a3a'
  return baseColor
}

function initials(name: string): string {
  return name
    .split(/[\s_]+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

/** One combat-grid token's face, at local (0,0) - the caller wraps it in a
 * translate(cx,cy) group and draws the identity label/turn ring on top (see
 * CombatGrid's own map call below) so both are visible regardless of which
 * branch here rendered. Renders the character's portrait clipped to the
 * shared circular clipPath, falling back to a plain colored circle (via
 * React state, not just CSS) whenever portraitUrl returns null - not yet
 * enough data to pick one - or the <image> itself fails to load, which is
 * the expected/common case until the portrait batch job has generated that
 * particular combination. */
function TokenFace({ character, baseColor }: { character: LiveCharacter; baseColor: string }) {
  const [imageFailed, setImageFailed] = useState(false)
  const url = portraitUrl(character)

  if (url && !imageFailed) {
    return (
      <image
        href={url}
        x={-TOKEN_RADIUS}
        y={-TOKEN_RADIUS}
        width={TOKEN_RADIUS * 2}
        height={TOKEN_RADIUS * 2}
        clipPath="url(#token-clip)"
        onError={() => setImageFailed(true)}
      />
    )
  }

  return <circle cx={0} cy={0} r={TOKEN_RADIUS} fill={tokenColor(character, baseColor)} />
}

export default function CombatGrid({
  battleMap,
  characters,
  currentActorId,
  myCharacterId,
  canMove,
  onMoveTo,
  actorColors,
}: {
  battleMap: LiveBattleMap
  characters: Record<string, LiveCharacter>
  currentActorId: string
  myCharacterId: string
  canMove: boolean
  onMoveTo: (to: { x: number; y: number }) => void
  actorColors: Record<string, string>
}) {
  const width = battleMap.width * CELL_SIZE
  const height = battleMap.height * CELL_SIZE
  const tokensByCell = new Map<string, LiveCharacter>()
  for (const character of Object.values(characters)) {
    tokensByCell.set(`${character.position.x},${character.position.y}`, character)
  }

  return (
    <svg
      className="combat-grid"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Combat grid"
    >
      <defs>
        <clipPath id="token-clip">
          <circle cx={0} cy={0} r={TOKEN_RADIUS} />
        </clipPath>
      </defs>
      {battleMap.terrain.map((row, y) =>
        row.map((terrain, x) => (
          <rect
            key={`${x},${y}`}
            x={x * CELL_SIZE}
            y={y * CELL_SIZE}
            width={CELL_SIZE}
            height={CELL_SIZE}
            fill={TERRAIN_FILL[terrain]}
            stroke="#2a2632"
            className={canMove ? 'grid-cell clickable' : 'grid-cell'}
            onClick={canMove ? () => onMoveTo({ x, y }) : undefined}
          />
        )),
      )}
      {Object.values(characters).map((character) => {
        if (character.is_dead) return null
        const cx = character.position.x * CELL_SIZE + CELL_SIZE / 2
        const cy = character.position.y * CELL_SIZE + CELL_SIZE / 2
        const isActing = character.id === currentActorId
        const stroke = isActing ? '#ffd166' : character.id === myCharacterId ? '#ffffff' : 'none'
        const strokeWidth = isActing ? 3 : 2
        return (
          <g key={character.id} className="grid-token-group" transform={`translate(${cx},${cy})`}>
            <TokenFace character={character} baseColor={actorColors[character.id] ?? '#ff6b6b'} />
            {stroke !== 'none' && (
              <circle cx={0} cy={0} r={TOKEN_RADIUS} fill="none" stroke={stroke} strokeWidth={strokeWidth} />
            )}
            {/* Identity label (found live: same-species tokens - e.g. three
                wolves - all load the identical portrait image once one's
                been generated, making them visually indistinguishable
                without this). Always drawn on top of the portrait, not
                just the no-portrait fallback. */}
            <rect
              x={-TOKEN_RADIUS}
              y={-TOKEN_RADIUS}
              width={TOKEN_RADIUS * 2}
              height={11}
              rx={3}
              fill="#0d0b12"
              fillOpacity={0.85}
            />
            <text
              x={0}
              y={-TOKEN_RADIUS + 8}
              textAnchor="middle"
              fontSize={9}
              fontWeight={700}
              fill="#ffffff"
            >
              {initials(character.name)}
            </text>
            <rect
              x={-CELL_SIZE / 2 + 3}
              y={CELL_SIZE / 2 - 10}
              width={CELL_SIZE - 6}
              height={4}
              fill="#3a3448"
            />
            <rect
              x={-CELL_SIZE / 2 + 3}
              y={CELL_SIZE / 2 - 10}
              width={Math.max(0, ((CELL_SIZE - 6) * character.hp) / Math.max(1, character.max_hp))}
              height={4}
              fill="#4caf50"
            />
          </g>
        )
      })}
    </svg>
  )
}
