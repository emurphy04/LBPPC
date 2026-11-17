// Discord sign-in: nothing from Discord becomes part of the account. The player's online name and email are their
// own (an LBPPC account must exist and be linked to Discord in its settings first); a Discord name or email is never
// used for them. Kratos's sign-in mappers read the provider's claims from std.extVar('claims').
local claims = std.extVar('claims');
{
  identity: {
    traits: {},
  },
}
