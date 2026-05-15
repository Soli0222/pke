#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "open3"
require "shellwords"

query = JSON.parse($stdin.read)

def fail_with(message)
  warn(message)
  exit 1
end

def run_command(*command)
  stdout, stderr, status = Open3.capture3(*command)
  return stdout if status.success?

  fail_with("#{command.shelljoin} failed: #{stderr.strip}")
end

def field_value(item, field, vault)
  command = ["op", "item", "get", item, "--fields", "label=#{field}", "--format", "json"]
  command += ["--vault", vault] if vault && !vault.empty?

  parsed = JSON.parse(run_command(*command))
  if parsed.is_a?(Array)
    match = parsed.find { |entry| entry["label"] == field || entry["id"] == field }
    return match["value"] if match && match.key?("value")
  elsif parsed.is_a?(Hash)
    return parsed["value"] if parsed.key?("value")
  end

  fail_with("field #{field.inspect} was not found in 1Password item #{item.inspect}")
end

def reference_value(reference)
  run_command("op", "read", reference)
end

reference = query["reference"]
item = query["item"]
field = query["field"]
file = query["file"]
vault = query["vault"]
repository = query["repository"]
secret_name = query["secret_name"]

unknown_keys = query.keys - %w[repository secret_name reference vault item field file]
fail_with("unknown onepassword keys for #{repository}/#{secret_name}: #{unknown_keys.join(", ")}") unless unknown_keys.empty?

value =
  if reference && !reference.empty?
    reference_value(reference)
  elsif item && field && !item.empty? && !field.empty?
    field_value(item, field, vault)
  elsif item && file && vault && !item.empty? && !file.empty? && !vault.empty?
    reference_value("op://#{vault}/#{item}/#{file}")
  else
    fail_with("onepassword source for #{repository}/#{secret_name} requires either reference, item+field, or vault+item+file")
  end

print JSON.generate({ "value" => value })
