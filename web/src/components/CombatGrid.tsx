import type { LiveBattleMap, LiveCharacter, TerrainType } from '../ws/sessionClient'

const CELL_SIZE = 40

const TERRAIN_FILL: Record<TerrainType, string> = {
  floor: '#1c1924',
  wall: '#0a090d',
  difficult: '#3a2f1a',
  hazard: '#3a1a1a',
}

function tokenColor(character: LiveCharacter): string {
  if (character.is_dead) return '#4a4552'
  if (character.hp <= 0) return '#8a7a3a'
  return character.is_pc ? '#4dabff' : '#ff6b6b'
}

function initials(name: string): string {
  return name
    .split(/[\s_]+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

export default function CombatGrid({
  battleMap,
  characters,
  currentActorId,
  myCharacterId,
  canMove,
  onMoveTo,
}: {
  battleMap: LiveBattleMap
  characters: Record<string, LiveCharacter>
  currentActorId: string
  myCharacterId: string
  canMove: boolean
  onMoveTo: (to: { x: number; y: number }) => void
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
      width={width}
      height={height}
      role="img"
      aria-label="Combat grid"
    >
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
        return (
          <g key={character.id} className="grid-token-group">
            <circle
              cx={cx}
              cy={cy}
              r={CELL_SIZE / 2 - 4}
              fill={tokenColor(character)}
              stroke={isActing ? '#ffd166' : character.id === myCharacterId ? '#ffffff' : 'none'}
              strokeWidth={isActing ? 3 : 2}
            />
            <text x={cx} y={cy + 4} textAnchor="middle" fontSize={11} fill="#0d0b12">
              {initials(character.name)}
            </text>
            <rect
              x={cx - CELL_SIZE / 2 + 3}
              y={cy + CELL_SIZE / 2 - 10}
              width={CELL_SIZE - 6}
              height={4}
              fill="#3a3448"
            />
            <rect
              x={cx - CELL_SIZE / 2 + 3}
              y={cy + CELL_SIZE / 2 - 10}
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
