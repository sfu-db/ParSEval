{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 27,
      "name": "$27",
      "type": "INTEGER"
    }
  ],
  "id": "4",
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
                "index": 6,
                "name": "$6",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "MCTD",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN_OR_EQUAL",
            "operator": ">=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 27,
                "name": "$27",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 100,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "LESS_THAN_OR_EQUAL",
            "operator": "<=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 27,
                "name": "$27",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 400,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Laboratory",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}