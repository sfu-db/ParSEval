{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "OTHER_FUNCTION",
      "operator": "SUBSTR",
      "type": "VARCHAR",
      "operands": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "VARCHAR"
        },
        {
          "kind": "LITERAL",
          "value": 1,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        },
        {
          "kind": "LITERAL",
          "value": 7,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ]
    },
    {
      "kind": "OTHER",
      "operator": "||",
      "type": "VARCHAR",
      "operands": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        },
        {
          "kind": "OTHER_FUNCTION",
          "operator": "SUBSTR",
          "type": "VARCHAR",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "VARCHAR"
            },
            {
              "kind": "LITERAL",
              "value": 8,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            },
            {
              "kind": "LITERAL",
              "value": 2,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        }
      ]
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "TR001",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "TR001_2_6",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "bond",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}