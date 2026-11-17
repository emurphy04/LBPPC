// What Kratos sends the password migration hook (password_migration_hook.py): the account's shadNet user ID, set at
// import and never changed by a player, rather than the name typed, so case and renames can't point it elsewhere.
// It's read from metadata_public: Kratos doesn't pass metadata_admin to webhook templates. Players can read their
// public metadata but never change it (only the admin API can).
function(ctx) {
  shadnet_user_id: ctx.identity.metadata_public.shadnet_user_id,
  password: ctx.password,
}
