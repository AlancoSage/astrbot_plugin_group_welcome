[中文](README.md) | English

# Group Welcome (astrbot_plugin_group_welcome)

Automatically sends a welcome message when a new member joins a group. Supports a global default welcome message, per-group welcome text and images, and group allowlist/denylist filtering.

## Features

- Automatically detects OneBot v11 (aiocqhttp) `group_increase` notice events.
- Three-layer welcome message resolution: per-group config → global default → skip.
- Per-group welcome messages support an attached image (URL or local path); image-only welcomes are also supported.
- Placeholders:
  - `{at}`: mention the new member (At segment, aiocqhttp only)
  - `{name}`: nickname of the new member
  - `{group}`: group ID
- Group allowlist/denylist filtering: denylist mode (groups in the list are skipped) or allowlist mode (only listed groups are welcomed), selected via a dropdown in the WebUI.
- Admin commands: set/view/delete the current group's welcome text and image directly in group chat; synced with the WebUI config.
- Per-group welcomes are managed as cards (template_list) in the WebUI: add, expand-edit, and delete per group.
- A failure to send a single welcome message does not affect the plugin; the error is logged.

## Trigger rules

The plugin only triggers on OneBot v11 `notice` events with `notice_type` set to `group_increase`. Other platform adapters do not provide this event type and will not trigger it.

## Quick start

1. Install the plugin into AstrBot's plugin directory and reload (or upload the zip in the WebUI).
2. Configure as needed in the plugin settings:
   - `global_welcome`: global default welcome message
   - `global_image`: global default welcome image (optional)
3. To restrict where it applies, configure `list_mode` + `blacklist` / `whitelist`.
4. To customize a specific group, click "Add entry" under the per-group list and fill in the group ID, welcome message, and image.

## Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `true` | Enable the welcome feature |
| `global_welcome` | `text` | `Welcome {at}!` | Global default welcome message, used when a group has no per-group config. Placeholders supported |
| `global_image` | `string` | `""` | Global default welcome image (URL or local path). Empty means no image |
| `list_mode` | `string` | `blacklist` | List mode: `blacklist` / `whitelist` (dropdown in the WebUI) |
| `blacklist` | `list[string]` | `[]` | Group denylist, effective in blacklist mode. Group IDs |
| `whitelist` | `list[string]` | `[]` | Group allowlist, effective in whitelist mode. Group IDs |
| `group_welcomes` | `template_list` | `[]` | Per-group welcome cards, each with group ID, welcome message, and image |

### `group_welcomes` entry fields

| Field | Type | Description |
|---|---|---|
| `group_id` | `string` | Group ID to customize |
| `welcome` | `text` | Welcome message for this group. Placeholders supported. Falls back to the global message when empty |
| `image` | `string` | Welcome image for this group (URL or local path). Falls back to the global image when empty |

Example (equivalent to adding one card in the WebUI):

```json
{
  "group_welcomes": [
    {
      "__template_key": "group_welcome",
      "group_id": "123456789",
      "welcome": "Welcome {at} to group {group}!",
      "image": "https://example.com/welcome.png"
    }
  ]
}
```

## Commands (admin only)

| Command | Description |
|---|---|
| `/欢迎设置 <message>` | Set the welcome message for the current group |
| `/欢迎图片 <URL or path>` | Set the welcome image for the current group: reply to an image message to use it (saved locally), or pass an image URL/local path; pass "无 / 清除 / 删除" to clear it |
| `/欢迎查看` | Show the current group's welcome message and image |
| `/欢迎删除` | Delete the current group's config and fall back to the global default |

## Output

When a new member joins, the plugin sends one welcome message containing:

1. The welcome text (placeholders replaced, `{at}` rendered as an At segment)
2. The welcome image (if configured)

## FAQ

- No welcome message received
  - Make sure you are using the aiocqhttp (OneBot v11) platform adapter; other adapters do not deliver join-notice events.
  - Make sure `enabled` is on and the group is not filtered by the denylist (or is in the allowlist).

- Welcome image not displayed
  - Make sure the image URL is reachable by QQ servers; local paths must match the AstrBot runtime environment.

- At segment not working
  - The `{at}` placeholder is only supported on aiocqhttp; QQ official API and other platforms do not support At segments.
