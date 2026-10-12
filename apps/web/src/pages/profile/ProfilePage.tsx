import { Alert, Button, Chip, Paper, Skeleton, Stack, Typography } from '@mui/material'
import { ProfileForm } from './ProfileForm'
import { SkillsSection } from './SkillsSection'
import { TargetsSection } from './TargetsSection'
import { useProfileQuery } from './useProfile'

const card = { p: { xs: 2, sm: 3 } }

/** Learner profile: details and preferences, current skills, and target skills. */
export default function ProfilePage() {
  const profile = useProfileQuery()

  return (
    <Stack spacing={3}>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
        <Typography variant="h1">Profile</Typography>
        {profile.data && <Chip label={`Version ${profile.data.version}`} size="small" />}
      </Stack>
      {profile.isPending ? (
        <Skeleton variant="rounded" height={320} aria-label="Loading profile" />
      ) : profile.isError ? (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => void profile.refetch()}>
              Retry
            </Button>
          }
        >
          {profile.error.message}
        </Alert>
      ) : profile.data === null ? (
        <Paper variant="outlined" sx={card}>
          <Typography variant="h2" gutterBottom>
            Create your learner profile
          </Typography>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            Your skills, goals, and weekly time decide what BrainBench curates for you. You add
            current and target skills once the profile exists.
          </Typography>
          <ProfileForm profile={null} />
        </Paper>
      ) : (
        <>
          <Paper variant="outlined" sx={card}>
            <ProfileForm key={profile.data.id} profile={profile.data} />
          </Paper>
          <Paper variant="outlined" sx={card}>
            <SkillsSection skills={profile.data.current_skills} />
          </Paper>
          <Paper variant="outlined" sx={card}>
            <TargetsSection targets={profile.data.target_skills} />
          </Paper>
        </>
      )}
    </Stack>
  )
}
