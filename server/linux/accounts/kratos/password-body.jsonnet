// After a password change or recovery on lbppc.com: account_hooks.py turns the old shadNet password off.
function(ctx) {
  shadnet_user_id: if std.objectHas(ctx.identity, 'metadata_public') && ctx.identity.metadata_public != null
                      && std.objectHas(ctx.identity.metadata_public, 'shadnet_user_id')
                   then ctx.identity.metadata_public.shadnet_user_id else null,
}
