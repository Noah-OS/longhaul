# Security

## Reporting a vulnerability

Please report it in private, not in a public issue: open the repository's **Security** tab and choose **Report a vulnerability**. That sends it to the maintainers only.

Say what you did, what happened and what you expected, with the Longhaul version (`longhaul doctor` prints it), your operating system and your Claude Code version. Leave out anything private: no tokens, no sign-in files, no text of your conversations.

You will get an answer within 7 days. A fix goes out as a new release, and the changelog names the problem once the fix is out.

## Which versions get fixes

The latest release only. `longhaul update` says whether you run it.

## What counts

Longhaul runs on your own machine, with your own Claude Code sign-ins. These are in scope:

- Longhaul printing, logging or sending message text, tool output, tokens or credentials.
- Longhaul writing or deleting files outside what [Privacy and security](https://github.com/Noah-OS/longhaul/wiki/Privacy-and-Security) lists.
- A chat, a repository's files or a tool's output making Longhaul run a command, change an account or delete a folder that the user did not ask for.
- The shim `bin/longhaul` running a command form it should refuse.
- A hook that lets a prompt through on an account the folder is not allowed on.

Problems in Claude Code itself go to Anthropic, not here.
