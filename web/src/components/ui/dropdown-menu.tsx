"use client";

import * as React from "react";
import { DropdownMenu as MenuPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

const DropdownMenu = MenuPrimitive.Root;
const DropdownMenuTrigger = MenuPrimitive.Trigger;

function DropdownMenuContent({ className, sideOffset = 6, align = "end", ...props }: React.ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        sideOffset={sideOffset}
        align={align}
        className={cn(
          "z-50 min-w-[13rem] rounded-xl border border-stone-200 bg-popover p-1 text-sm text-stone-800 shadow-lg",
          "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

function DropdownMenuItem({ className, danger, ...props }: React.ComponentProps<typeof MenuPrimitive.Item> & { danger?: boolean }) {
  return (
    <MenuPrimitive.Item
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 whitespace-nowrap outline-none select-none",
        "data-[highlighted]:bg-stone-100 dark:data-[highlighted]:bg-white/10 data-[disabled]:pointer-events-none data-[disabled]:opacity-45",
        "[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-stone-500",
        danger && "text-rose-600 [&_svg]:text-rose-500 dark:text-rose-400",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuSeparator({ className, ...props }: React.ComponentProps<typeof MenuPrimitive.Separator>) {
  return <MenuPrimitive.Separator className={cn("my-1 h-px bg-stone-100", className)} {...props} />;
}

export { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger };
