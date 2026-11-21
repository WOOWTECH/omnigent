import { useCallback, useEffect, useState, type FormEvent } from "react";
import { CopyIcon, KeyRoundIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { getCurrentIsAdmin, resolveIdentity } from "@/lib/identity";
import {
  createMcpServerProfile,
  listMcpServerProfiles,
  revokeMcpServerProfile,
  type McpServerProfile,
} from "@/lib/mcpServerApi";

function formatTime(value: number | null): string {
  if (value === null) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}

export function McpServerPage() {
  const info = useServerInfo();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [profiles, setProfiles] = useState<McpServerProfile[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdLink, setCreatedLink] = useState<{ name: string; url: string } | null>(null);

  const refresh = useCallback(async () => {
    const result = await listMcpServerProfiles();
    if (!result.ok) {
      setProfiles([]);
      setLoadError(result.error);
      return;
    }
    setProfiles(result.value);
    setLoadError(null);
  }, []);

  useEffect(() => {
    void (async () => {
      const userId = await resolveIdentity();
      if (userId === null) return;
      const admin = getCurrentIsAdmin();
      setIsAdmin(admin);
      if (admin) await refresh();
    })();
  }, [refresh]);

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    const result = await createMcpServerProfile({ name, username, password });
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    const url = result.value.connection_url;
    setCreatedLink({ name: result.value.profile.name, url });
    setName("");
    setPassword("");
    await refresh();
  }

  async function onRevoke(profile: McpServerProfile) {
    setBusy(true);
    setActionError(null);
    const result = await revokeMcpServerProfile(profile.id);
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error);
      return;
    }
    await refresh();
  }

  async function copyLink() {
    if (!createdLink) return;
    try {
      await navigator.clipboard.writeText(createdLink.url);
    } catch {
      setActionError("Could not copy the URL. Select and copy it manually.");
    }
  }

  const localHttp =
    typeof window !== "undefined" &&
    window.location.protocol !== "https:" &&
    !["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);

  if (info !== "loading" && !info.accounts_enabled) {
    return (
      <PageScroll maxWidthClassName="max-w-4xl" contentClassName="px-4 md:px-8">
        <h1 className="settings-page-title mb-2 text-2xl font-medium">Omnigent MCP</h1>
        <p className="text-ui text-muted-foreground">
          MCP profiles currently require Omnigent accounts authentication.
        </p>
      </PageScroll>
    );
  }

  if (isAdmin === null) {
    return (
      <div className="flex min-h-full items-center justify-center text-ui text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <PageScroll maxWidthClassName="max-w-4xl" contentClassName="px-4 md:px-8">
        <h1 className="settings-page-title mb-2 text-2xl font-medium">Omnigent MCP</h1>
        <p className="text-ui text-muted-foreground">
          You don't have permission to manage MCP profiles.
        </p>
      </PageScroll>
    );
  }

  return (
    <PageScroll maxWidthClassName="max-w-4xl" contentClassName="px-4 md:px-8" extraBottom="2.5rem">
      <h1 className="settings-page-title mb-2 text-2xl font-medium">Omnigent MCP</h1>
      <p className="mb-6 text-ui text-muted-foreground">
        Create revocable Streamable HTTP links for external agents. Each profile runs with the
        linked Omnigent account's existing access to sessions, sharing, and scheduled automations.
      </p>

      <section className="mb-8 rounded-xl border border-border p-4 md:p-5">
        <div className="mb-4 flex items-center gap-2">
          <KeyRoundIcon className="size-5 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-lg font-medium">Create profile</h2>
        </div>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={onCreate}>
          <label className="grid gap-1.5 text-sm sm:col-span-2">
            Profile name
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={128}
            />
          </label>
          <label className="grid gap-1.5 text-sm">
            Omnigent account
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="grid gap-1.5 text-sm">
            Account password
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <div className="flex flex-col gap-3 sm:col-span-2">
            <p className="text-xs text-muted-foreground">
              The password is used once to verify the account and is not saved. The generated URL is
              shown once; Omnigent stores only a hash of its token.
            </p>
            {localHttp && (
              <p className="text-xs text-amber-600">
                This page is using HTTP. Serve Omnigent over HTTPS before sharing an MCP URL.
              </p>
            )}
            {actionError && <p className="text-sm text-destructive">{actionError}</p>}
            <Button
              type="submit"
              className="w-fit"
              disabled={busy || localHttp || !name || !username || !password}
            >
              {busy ? (
                <Loader2Icon className="mr-2 size-4 animate-spin" />
              ) : (
                <PlusIcon className="mr-2 size-4" />
              )}
              Create MCP link
            </Button>
          </div>
        </form>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">Profiles</h2>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void refresh()}
            disabled={busy}
          >
            Refresh
          </Button>
        </div>
        {loadError && <p className="mb-3 text-sm text-destructive">{loadError}</p>}
        {profiles === null ? (
          <p className="text-sm text-muted-foreground">Loading profiles…</p>
        ) : profiles.length === 0 ? (
          loadError ? null : (
            <p className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">
              No MCP profiles yet.
            </p>
          )
        ) : (
          <div className="grid gap-3">
            {profiles.map((profile) => (
              <article key={profile.id} className="rounded-xl border border-border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{profile.name}</h3>
                      <Badge variant={profile.active ? "secondary" : "outline"}>
                        {profile.active ? "Active" : "Revoked"}
                      </Badge>
                    </div>
                    <p className="break-all text-sm text-muted-foreground">{profile.user_id}</p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Created {formatTime(profile.created_at)} · Last used{" "}
                      {formatTime(profile.last_used_at)}
                      {!profile.active && ` · Revoked ${formatTime(profile.revoked_at)}`}
                    </p>
                  </div>
                  {profile.active && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => void onRevoke(profile)}
                    >
                      <Trash2Icon className="mr-2 size-4" />
                      Revoke
                    </Button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <Dialog open={createdLink !== null} onOpenChange={(open) => !open && setCreatedLink(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>MCP URL created for {createdLink?.name}</DialogTitle>
            <DialogDescription>
              Copy this URL now and add it to the external agent. It will not be shown again. Anyone
              with the URL can use the linked account's Omnigent permissions.
            </DialogDescription>
          </DialogHeader>
          <div className="break-all rounded-md bg-muted p-3 font-mono text-xs">
            {createdLink?.url}
          </div>
          {actionError && <p className="text-sm text-destructive">{actionError}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setCreatedLink(null)}>
              Done
            </Button>
            <Button type="button" onClick={() => void copyLink()}>
              <CopyIcon className="mr-2 size-4" />
              Copy URL
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageScroll>
  );
}
