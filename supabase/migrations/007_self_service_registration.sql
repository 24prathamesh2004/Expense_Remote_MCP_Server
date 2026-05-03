-- Self-service user registration and API key management
-- Allows users to register and get API keys via MCP tool

-- ---------------------------------------------------------------------------
-- API Keys Table
-- ---------------------------------------------------------------------------
create table if not exists public.api_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  api_key text not null unique,
  key_name text not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  expires_at timestamptz,
  is_active boolean not null default true
);

create index if not exists idx_api_keys_key on public.api_keys(api_key) where is_active = true;
create index if not exists idx_api_keys_user on public.api_keys(user_id);

-- ---------------------------------------------------------------------------
-- RLS for API Keys
-- ---------------------------------------------------------------------------
alter table public.api_keys enable row level security;

-- Users can only see their own API keys
create policy api_keys_select_own on public.api_keys
  for select using (auth.uid() = user_id);

-- Users can delete their own API keys
create policy api_keys_delete_own on public.api_keys
  for delete using (auth.uid() = user_id);

-- No direct insert/update (use RPCs)
create policy api_keys_no_insert on public.api_keys
  for insert with check (false);

create policy api_keys_no_update on public.api_keys
  for update using (false);

-- ---------------------------------------------------------------------------
-- RPC: Register new user and generate API key
-- ---------------------------------------------------------------------------
create or replace function public.fn_register_user(
  p_email text,
  p_password text,
  p_full_name text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_api_key text;
  v_error text;
begin
  -- Validate email format
  if p_email !~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' then
    return jsonb_build_object(
      'status', 'error',
      'message', 'Invalid email format'
    );
  end if;

  -- Validate password strength (min 8 chars)
  if length(p_password) < 8 then
    return jsonb_build_object(
      'status', 'error',
      'message', 'Password must be at least 8 characters'
    );
  end if;

  -- Check if user already exists
  if exists (
    select 1 from auth.users where email = lower(trim(p_email))
  ) then
    return jsonb_build_object(
      'status', 'error',
      'message', 'User already exists. Use login tool instead.'
    );
  end if;

  -- Create user in auth.users
  begin
    insert into auth.users (
      instance_id,
      id,
      aud,
      role,
      email,
      encrypted_password,
      email_confirmed_at,
      raw_app_meta_data,
      raw_user_meta_data,
      created_at,
      updated_at,
      confirmation_token,
      recovery_token
    )
    values (
      '00000000-0000-0000-0000-000000000000',
      gen_random_uuid(),
      'authenticated',
      'authenticated',
      lower(trim(p_email)),
      crypt(p_password, gen_salt('bf')),
      now(),
      '{"provider":"email","providers":["email"]}'::jsonb,
      jsonb_build_object('full_name', p_full_name),
      now(),
      now(),
      '',
      ''
    )
    returning id into v_user_id;
  exception when others then
    get stacked diagnostics v_error = message_text;
    return jsonb_build_object(
      'status', 'error',
      'message', 'Failed to create user: ' || v_error
    );
  end;

  -- Generate API key
  v_api_key := 'exp_' || encode(gen_random_bytes(32), 'base64');
  v_api_key := replace(replace(replace(v_api_key, '+', ''), '/', ''), '=', '');

  -- Store API key
  insert into public.api_keys (user_id, api_key, key_name)
  values (v_user_id, v_api_key, 'Default Key');

  return jsonb_build_object(
    'status', 'success',
    'user_id', v_user_id,
    'email', p_email,
    'api_key', v_api_key,
    'message', 'Registration successful! Save your API key - it will not be shown again.'
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: Login and get API key (for existing users)
-- ---------------------------------------------------------------------------
create or replace function public.fn_login_get_key(
  p_email text,
  p_password text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_api_key text;
  v_password_hash text;
begin
  -- Get user
  select id, encrypted_password into v_user_id, v_password_hash
  from auth.users
  where email = lower(trim(p_email))
    and deleted_at is null;

  if not found then
    return jsonb_build_object(
      'status', 'error',
      'message', 'Invalid email or password'
    );
  end if;

  -- Verify password
  if v_password_hash != crypt(p_password, v_password_hash) then
    return jsonb_build_object(
      'status', 'error',
      'message', 'Invalid email or password'
    );
  end if;

  -- Check if user already has an active API key
  select api_key into v_api_key
  from public.api_keys
  where user_id = v_user_id
    and is_active = true
    and (expires_at is null or expires_at > now())
  order by created_at desc
  limit 1;

  -- If no active key, generate new one
  if v_api_key is null then
    v_api_key := 'exp_' || encode(gen_random_bytes(32), 'base64');
    v_api_key := replace(replace(replace(v_api_key, '+', ''), '/', ''), '=', '');

    insert into public.api_keys (user_id, api_key, key_name)
    values (v_user_id, v_api_key, 'Login Key - ' || to_char(now(), 'YYYY-MM-DD'));
  end if;

  return jsonb_build_object(
    'status', 'success',
    'user_id', v_user_id,
    'email', p_email,
    'api_key', v_api_key,
    'message', 'Login successful! Use this API key in your Claude Desktop config.'
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: Validate API key and get user info
-- ---------------------------------------------------------------------------
create or replace function public.fn_validate_api_key(p_api_key text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_email text;
begin
  -- Look up API key
  select ak.user_id, u.email
  into v_user_id, v_email
  from public.api_keys ak
  join auth.users u on u.id = ak.user_id
  where ak.api_key = p_api_key
    and ak.is_active = true
    and (ak.expires_at is null or ak.expires_at > now())
    and u.deleted_at is null;

  if not found then
    return jsonb_build_object(
      'status', 'error',
      'message', 'Invalid or expired API key'
    );
  end if;

  -- Update last used timestamp
  update public.api_keys
  set last_used_at = now()
  where api_key = p_api_key;

  return jsonb_build_object(
    'status', 'success',
    'user_id', v_user_id,
    'email', v_email
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- RPC: Revoke API key
-- ---------------------------------------------------------------------------
create or replace function public.fn_revoke_api_key(p_api_key text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.api_keys
  set is_active = false
  where api_key = p_api_key
    and user_id = auth.uid();

  if not found then
    return jsonb_build_object(
      'status', 'error',
      'message', 'API key not found or not owned by you'
    );
  end if;

  return jsonb_build_object(
    'status', 'success',
    'message', 'API key revoked successfully'
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Grants
-- ---------------------------------------------------------------------------
grant execute on function public.fn_register_user(text, text, text) to anon;
grant execute on function public.fn_login_get_key(text, text) to anon;
grant execute on function public.fn_validate_api_key(text) to anon;
grant execute on function public.fn_revoke_api_key(text) to authenticated;

-- ---------------------------------------------------------------------------
-- Note: This allows anonymous registration
-- For production, consider adding:
-- 1. Email verification
-- 2. Rate limiting (max 5 registrations per IP per day)
-- 3. CAPTCHA integration
-- 4. Admin approval workflow
-- ---------------------------------------------------------------------------
